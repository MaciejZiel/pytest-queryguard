"""The SQLite index check with aiosqlite and with pytest-xdist workers."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# pytester drops modules first imported by an inner run from sys.modules when
# that run ends; importing the async stack here keeps it loaded.
pytest.importorskip("aiosqlite")
pytest.importorskip("greenlet")
import sqlalchemy.ext.asyncio  # noqa: F401

pytestmark = pytest.mark.allow_nplusone

MODELS = (Path(__file__).parent / "models.py").read_text()
ARGS = ("--queryguard-index-check",)


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makepyfile(qgmodels=MODELS)
    return pytester


ASYNC_HELPERS = """
import asyncio
import sqlite3

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from qgmodels import Base, Order


async def with_session(body):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            session.add_all(Order(id=i, customer_id=i % 3) for i in range(1, 7))
            await session.commit()
            return await body(session)
    finally:
        await engine.dispose()
"""


def test_async_scan_is_a_finding(project: pytest.Pytester) -> None:
    project.makepyfile(
        test_a=ASYNC_HELPERS
        + """

def test_async_scan():
    async def body(session):
        result = await session.execute(select(Order).where(Order.customer_id == 1))
        return len(result.scalars().all())

    assert asyncio.run(with_session(body)) == 2
"""
    )
    result = project.runpytest(*ARGS)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "*orders is read in full: SCAN with filter orders.customer_id = ?",
            "*fix: CREATE INDEX ix_orders_customer_id ON orders (customer_id)",
        ]
    )


def test_async_with_index_and_primary_key_passes(project: pytest.Pytester) -> None:
    project.makepyfile(
        test_a=ASYNC_HELPERS
        + """

def test_async_ok():
    async def body(session):
        await session.execute(text("CREATE INDEX ix_c ON orders (customer_id)"))
        a = await session.execute(select(Order).where(Order.customer_id == 1))
        b = await session.get(Order, 3)
        return len(a.scalars().all()), b.id

    assert asyncio.run(with_session(body)) == (2, 3)
"""
    )
    project.runpytest(*ARGS).assert_outcomes(passed=1)


def test_async_transaction_and_data_are_untouched(project: pytest.Pytester) -> None:
    project.makepyfile(
        test_a=ASYNC_HELPERS
        + """
import pytest


@pytest.mark.allow_missing_index("orders")
def test_async_transaction():
    async def body(session):
        await session.execute(
            update(Order).where(Order.customer_id == 1).values(customer_id=9)
        )
        # the update is still pending in the session's transaction
        seen = (await session.execute(
            text("SELECT count(*) FROM orders WHERE customer_id = 9")
        )).scalar_one()
        await session.rollback()
        after = (await session.execute(
            text("SELECT count(*) FROM orders WHERE customer_id = 9")
        )).scalar_one()
        return seen, after

    assert asyncio.run(with_session(body)) == (2, 0)
"""
    )
    project.runpytest(*ARGS).assert_outcomes(passed=1)


# ------------------------------------------------------------ xdist

XDIST_TESTS = """
import pytest
from qgmodels import Order, make_sync_session


@pytest.mark.parametrize("i", range(6))
def test_scan(i):
    make_sync_session().query(Order).filter(Order.customer_id == 1).all()


@pytest.mark.parametrize("i", range(6))
def test_by_key(i):
    make_sync_session().get(Order, 10)
"""


@pytest.mark.parametrize("workers", [2, 3])
def test_xdist_workers_report_the_same_tests(
    project: pytest.Pytester, workers: int
) -> None:
    pytest.importorskip("xdist")
    project.makepyfile(test_a=XDIST_TESTS)

    def failed(result: pytest.RunResult) -> set[str]:
        return set(
            re.findall(r"^FAILED (\S+?)(?: - |$)", result.stdout.str(), re.MULTILINE)
        )

    serial = project.runpytest_subprocess(*ARGS, "-p", "no:cacheprovider")
    parallel = project.runpytest_subprocess(
        *ARGS, "-n", str(workers), "-p", "no:cacheprovider"
    )
    assert serial.ret == parallel.ret == 1
    assert len(failed(parallel)) == 6
    assert failed(serial) == failed(parallel)
    parallel.stdout.fnmatch_lines(
        ["*fix: CREATE INDEX ix_orders_customer_id ON orders (customer_id)"]
    )
