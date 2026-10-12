"""Concurrent units never mix statements: asyncio tasks, threads, both."""

from __future__ import annotations

import asyncio
import contextvars
import threading
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import literal_column, select
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import Session

from pytest_queryguard import app as queryguard
from pytest_queryguard.app import RequestReport

from .conftest import Order

UNITS = 50


def _marker(unit: int, step: int) -> str:
    # A statement text that says which unit ran it: SELECT 1007 is unit 7.
    return f"SELECT {1000 * (step + 1) + unit}"


def _check(reports: dict[int, RequestReport], expected: dict[int, int]) -> None:
    assert sorted(reports) == sorted(expected)
    for unit, report in reports.items():
        assert report.statement_count == expected[unit], unit
        mine = {_marker(unit, step) for step in range(expected[unit])}
        assert {q.sql.split(" AS ")[0] for q in report.statements} == mine, unit


def test_asyncio_gather_of_50_units(watched: Engine, async_engine: AsyncEngine) -> None:
    queryguard.watch(async_engine)

    async def unit(n: int) -> RequestReport:
        with queryguard.track(f"unit {n}", report=[]) as report:
            async with AsyncSession(async_engine) as session:
                for step in range(n % 7 + 1):
                    await session.execute(
                        select(literal_column(str(1000 * (step + 1) + n)))
                    )
                    await asyncio.sleep(0)  # let the other units interleave
        return report

    async def main() -> list[RequestReport]:
        try:
            return await asyncio.gather(*(unit(n) for n in range(UNITS)))
        finally:
            await async_engine.dispose()

    reports = asyncio.run(main())
    _check({n: r for n, r in enumerate(reports)}, {n: n % 7 + 1 for n in range(UNITS)})


def test_asyncio_n_plus_one_is_found_per_unit(async_engine: AsyncEngine) -> None:
    queryguard.watch(async_engine)

    async def unit(lazy: bool) -> RequestReport:
        with queryguard.track(report=[]) as report:
            async with AsyncSession(async_engine) as session:
                orders = (await session.scalars(select(Order))).all()
                if lazy:
                    for order in orders:
                        await order.awaitable_attrs.customer
                        await asyncio.sleep(0)
        return report

    async def main() -> list[RequestReport]:
        try:
            return await asyncio.gather(*(unit(n % 2 == 0) for n in range(20)))
        finally:
            await async_engine.dispose()

    reports = asyncio.run(main())
    for n, report in enumerate(reports):
        if n % 2 == 0:
            assert [f.label for f in report.findings] == ["Order.customer"], n
            assert report.statement_count == 6
        else:
            assert report.findings == (), n
            assert report.statement_count == 1


def test_thread_pool_of_50_units(watched: Engine) -> None:
    barrier = threading.Barrier(8)

    def unit(n: int) -> RequestReport:
        with (
            queryguard.track(f"unit {n}", report=[]) as report,
            Session(watched) as session,
        ):
            for step in range(n % 7 + 1):
                if step == 0 and n < 8:
                    barrier.wait(timeout=10)  # the first eight run together
                session.execute(select(literal_column(str(1000 * (step + 1) + n))))
        return report

    with ThreadPoolExecutor(max_workers=8) as pool:
        reports = list(pool.map(unit, range(UNITS)))
    _check({n: r for n, r in enumerate(reports)}, {n: n % 7 + 1 for n in range(UNITS)})


def test_mixed_tasks_threads_and_a_thread_without_the_context(
    watched: Engine, async_engine: AsyncEngine
) -> None:
    """A unit's threads count when they copy the context; a bare thread never."""
    queryguard.watch(async_engine)

    def sync_part(n: int, steps: range) -> None:
        with Session(watched) as session:
            for step in steps:
                session.execute(select(literal_column(str(1000 * (step + 1) + n))))

    async def unit(n: int) -> RequestReport:
        with queryguard.track(f"unit {n}", report=[]) as report:
            async with AsyncSession(async_engine) as session:
                await session.execute(select(literal_column(str(1000 + n))))
            # asyncio.to_thread copies the context: these count for the unit.
            await asyncio.to_thread(sync_part, n, range(1, 3))
            # A plain thread starts with an empty context: never attributed.
            bare = threading.Thread(target=sync_part, args=(n + 500, range(1)))
            bare.start()
            bare.join()
            # A thread given a copy of the context counts again.
            ctx = contextvars.copy_context()
            copied = threading.Thread(target=ctx.run, args=(sync_part, n, range(3, 4)))
            copied.start()
            copied.join()
        return report

    async def main() -> list[RequestReport]:
        try:
            return await asyncio.gather(*(unit(n) for n in range(UNITS)))
        finally:
            await async_engine.dispose()

    reports = asyncio.run(main())
    _check({n: r for n, r in enumerate(reports)}, dict.fromkeys(range(UNITS), 4))


def test_nested_track_takes_the_statements_of_its_block(watched: Engine) -> None:
    with queryguard.track("outer", report=[]) as outer, Session(watched) as session:
        session.execute(select(literal_column("1")))
        with queryguard.track("inner", report=[]) as inner:
            session.execute(select(literal_column("2")))
        session.execute(select(literal_column("3")))
    assert outer.statement_count == 2
    assert inner.statement_count == 1
