"""Concurrent units never mix statements: asyncio tasks, threads, both."""

from __future__ import annotations

import asyncio
import contextvars
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import literal_column, select
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import Session

from pytest_queryguard import app as queryguard
from pytest_queryguard.app import RequestReport, _core

from .conftest import Order

UNITS = 50

# Python 3.14 can start threads with a copy of the caller's context: the
# default on free-threaded builds, or -X thread_inherit_context=1. Then a plain
# thread, and a pool worker, keep the context of the code that created them.
THREADS_INHERIT_CONTEXT = bool(getattr(sys.flags, "thread_inherit_context", 0))


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
    """A unit's threads count when they copy the context; a bare thread only
    where threads inherit it, and then for its own unit."""
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
            # A plain thread starts with an empty context and is never
            # attributed, unless threads inherit the context (see above).
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
    if not THREADS_INHERIT_CONTEXT:
        _check({n: r for n, r in enumerate(reports)}, dict.fromkeys(range(UNITS), 4))
        return
    # The plain thread inherited its unit's context, so it counts for that
    # unit and still for no other.
    for n, report in enumerate(reports):
        mine = {_marker(n, step) for step in range(4)} | {_marker(n + 500, 0)}
        assert report.statement_count == 5, n
        assert {q.sql.split(" AS ")[0] for q in report.statements} == mine, n


def test_a_pool_task_run_in_a_copied_context_counts_for_its_unit(
    watched: Engine,
) -> None:
    """copy_context().run per task attributes it right, inherited context or not.

    The worker is created inside unit 0. Where threads inherit the context it
    keeps unit 0's, so a plain submit() from a later unit would not count for
    that unit; running each task in a copy of the submitter's context does.
    """

    def sync_part(n: int) -> None:
        with Session(watched) as session:
            session.execute(select(literal_column(str(1000 + n))))

    reports: dict[int, RequestReport] = {}
    with ThreadPoolExecutor(max_workers=1) as pool:
        for n in range(3):
            with queryguard.track(f"unit {n}", report=[]) as report:
                ctx = contextvars.copy_context()
                pool.submit(ctx.run, sync_part, n).result(timeout=10)
            reports[n] = report
    _check(reports, dict.fromkeys(range(3), 1))


def test_nested_track_takes_the_statements_of_its_block(watched: Engine) -> None:
    with queryguard.track("outer", report=[]) as outer, Session(watched) as session:
        session.execute(select(literal_column("1")))
        with queryguard.track("inner", report=[]) as inner:
            session.execute(select(literal_column("2")))
        session.execute(select(literal_column("3")))
    assert outer.statement_count == 2
    assert inner.statement_count == 1


def test_a_finished_units_context_records_nothing(watched: Engine) -> None:
    """A thread that keeps a finished unit's context adds nothing to it.

    This is what a pool worker created inside a request does on builds where
    threads inherit the context; copy_context() reproduces it on every build.
    """
    with queryguard.track("unit", report=[]) as report:
        with Session(watched) as session:
            session.execute(select(literal_column("1001")))
        kept = contextvars.copy_context()

    def late() -> int:
        with Session(watched) as session:
            session.execute(select(literal_column("2001")))
        active = _core.current.get()
        assert active is not None
        return active.collector.statement_count

    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(kept.run, late).result(timeout=10) == 1
    assert report.statement_count == 1
