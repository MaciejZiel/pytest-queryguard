"""assert_queries_do_not_grow(): the statement count must not grow with the size."""

from __future__ import annotations

import asyncio
import threading
import warnings
from collections.abc import Callable, Iterator

import pytest
from sqlalchemy import Engine, create_engine, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, joinedload, sessionmaker
from sqlalchemy.pool import StaticPool

from pytest_queryguard import (
    QueryBudgetExceeded,
    QueryLog,
    assert_queries_do_not_grow,
    assert_queries_do_not_grow_async,
)
from tests.models import Base, Customer, Order

# The outer plugin measures these tests too; some cause N+1 on purpose.
pytestmark = pytest.mark.allow_nplusone


def _engine() -> Engine:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    return engine


def _seed(session: Session, rows: int) -> None:
    ids = range(1, rows + 1)
    session.add_all(Customer(id=i, name=f"customer {i}") for i in ids)
    session.add_all(Order(id=i, customer_id=i) for i in ids)
    session.commit()
    session.expunge_all()


@pytest.fixture
def make_session() -> Iterator[Callable[[int], Session]]:
    sessions: list[Session] = []

    def make(rows: int) -> Session:
        session = sessionmaker(_engine())()
        _seed(session, rows)
        sessions.append(session)
        return session

    yield make
    for session in sessions:
        session.close()


def list_orders(session: Session) -> list[str]:
    query = select(Order).options(joinedload(Order.customer))
    return [order.customer.name for order in session.scalars(query)]


def list_orders_lazy(session: Session) -> list[str]:
    return [order.customer.name for order in session.scalars(select(Order))]


def test_a_constant_count_passes_and_returns_the_log_of_each_size(
    make_session: Callable[[int], Session],
) -> None:
    logs = assert_queries_do_not_grow(
        list_orders, sizes=(10, 1, 50), setup=make_session
    )
    assert list(logs) == [1, 10, 50]
    assert all(isinstance(log, QueryLog) for log in logs.values())
    assert [log.count for log in logs.values()] == [1, 1, 1]


def test_setup_is_not_recorded(make_session: Callable[[int], Session]) -> None:
    # the seeding sends one INSERT per row: counted, it would grow
    logs = assert_queries_do_not_grow(list_orders, setup=make_session)
    assert all("INSERT" not in query.sql for log in logs.values() for query in log)


def test_a_lazy_load_per_row_fails_with_the_counts_and_the_fix(
    make_session: Callable[[int], Session],
) -> None:
    with pytest.raises(QueryBudgetExceeded) as info:
        assert_queries_do_not_grow(
            list_orders_lazy, sizes=(1, 10, 50), setup=make_session
        )
    message = str(info.value)
    lines = message.splitlines()
    assert lines[0] == (
        "queryguard: the number of statements grows with the size: "
        "2 at size 1, 11 at size 10, 51 at size 50"
    )
    assert (
        "  SELECT customers.id, customers.name FROM customers "
        "WHERE customers.id = ?" in lines
    )
    assert (
        "  ran 1 time at size 1, 10 times at size 10, 50 times at size 50 "
        "(loads Order.customer)" in lines
    )
    first = next(line for line in lines if line.startswith("  first at "))
    assert first.endswith(" in list_orders_lazy")
    assert "test_queries_do_not_grow.py:" in first
    assert "  fix: load Order.customer with the parent query, e.g." in lines
    assert "selectinload(Order.customer)" in message
    # the order query does not grow, so it is not listed
    assert "FROM orders" not in message
    assert lines[-1].endswith("/recipes-query-count-scaling.html")


def test_without_setup_fn_gets_the_size() -> None:
    engine = _engine()
    seen = []

    def lookups(size: int) -> None:
        seen.append(size)
        with engine.connect() as conn:
            for i in range(size):
                conn.execute(select(Customer).where(Customer.id == i))

    with pytest.raises(QueryBudgetExceeded) as info:
        assert_queries_do_not_grow(lookups, sizes=[2, 5])
    assert seen == [2, 5]
    message = str(info.value)
    assert "2 at size 2, 5 at size 5" in message
    assert "ran 2 times at size 2, 5 times at size 5" in message
    assert "fix: fetch all rows in one query (WHERE id IN (...))" in message


def test_a_write_per_row_gets_the_write_fix() -> None:
    engine = _engine()

    def add(size: int) -> None:
        with engine.begin() as conn:
            for i in range(size):
                conn.execute(insert(Customer).values(id=1000 * size + i, name="x"))

    with pytest.raises(QueryBudgetExceeded, match="write the rows together"):
        assert_queries_do_not_grow(add, sizes=(1, 3))


def test_fewer_statements_at_a_larger_size_pass() -> None:
    engine = _engine()

    def shrinking(size: int) -> None:
        with engine.connect() as conn:
            for _ in range(10 - size):
                conn.execute(text("select 1"))

    logs = assert_queries_do_not_grow(shrinking, sizes=(1, 5))
    assert [log.count for log in logs.values()] == [9, 5]


def test_statements_from_other_threads_count() -> None:
    engine = _engine()

    def threaded(size: int) -> None:
        def work() -> None:
            with engine.connect() as conn:
                for _ in range(size):
                    conn.execute(text("select 1"))

        thread = threading.Thread(target=work)
        thread.start()
        thread.join()

    with pytest.raises(QueryBudgetExceeded, match="1 at size 1, 4 at size 4"):
        assert_queries_do_not_grow(threaded, sizes=(1, 4))


def test_an_exception_in_fn_propagates() -> None:
    def broken(size: int) -> None:
        raise KeyError(size)

    with pytest.raises(KeyError):
        assert_queries_do_not_grow(broken)


@pytest.mark.parametrize(
    ("sizes", "error", "match"),
    [
        ((1,), ValueError, "at least two sizes"),
        ((), ValueError, "at least two sizes"),
        ((1, 1), ValueError, "different"),
        ((1, -1), ValueError, "non-negative integer"),
        ((1, 2.5), ValueError, "non-negative integer"),
        ((True, 2), ValueError, "non-negative integer"),
        (10, TypeError, "iterable of integers"),
        ("12", TypeError, "iterable of integers"),
    ],
)
def test_bad_sizes_are_rejected(sizes: object, error: type, match: str) -> None:
    with pytest.raises(error, match=match):
        assert_queries_do_not_grow(lambda size: None, sizes=sizes)  # type: ignore[arg-type]


def test_bad_callables_are_rejected() -> None:
    with pytest.raises(TypeError, match="fn must be callable"):
        assert_queries_do_not_grow(None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="setup must be None or callable"):
        assert_queries_do_not_grow(lambda x: None, setup=3)  # type: ignore[arg-type]


def test_a_coroutine_function_needs_the_async_variant() -> None:
    async def fn(size: int) -> None:
        return None

    async def setup(size: int) -> int:
        return size

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # no "coroutine was never awaited"
        with pytest.raises(TypeError, match="fn returned an awaitable"):
            assert_queries_do_not_grow(fn)
        with pytest.raises(TypeError, match="setup returned an awaitable"):
            assert_queries_do_not_grow(lambda x: None, setup=setup)


def test_an_internal_error_is_not_a_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine()
    boom = RuntimeError("boom")
    monkeypatch.setattr(
        "pytest_queryguard.scaling.QueryLog._incomplete", lambda self: boom
    )

    def fn(size: int) -> None:
        with engine.connect() as conn:
            conn.execute(text("select 1"))

    with pytest.raises(RuntimeError, match="may be incomplete"):
        assert_queries_do_not_grow(fn)


async def _async_session(rows: int) -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    ids = range(1, rows + 1)
    session.add_all(Customer(id=i, name=f"customer {i}") for i in ids)
    session.add_all(Order(id=i, customer_id=i) for i in ids)
    await session.commit()
    session.expunge_all()
    return session


async def _names_in_a_loop(session: AsyncSession) -> list[str]:
    names = []
    for order in (await session.scalars(select(Order))).all():
        customer = await session.scalar(
            select(Customer).where(Customer.id == order.customer_id)
        )
        assert customer is not None
        names.append(customer.name)
    return names


async def _names_joined(session: AsyncSession) -> list[str]:
    query = select(Order).options(joinedload(Order.customer))
    return [order.customer.name for order in (await session.scalars(query)).all()]


def test_async_passes_and_fails() -> None:
    async def main() -> None:
        logs = await assert_queries_do_not_grow_async(
            _names_joined, sizes=(1, 6), setup=_async_session
        )
        assert [log.count for log in logs.values()] == [1, 1]
        with pytest.raises(QueryBudgetExceeded) as info:
            await assert_queries_do_not_grow_async(
                _names_in_a_loop, sizes=(1, 6), setup=_async_session
            )
        message = str(info.value)
        assert "2 at size 1, 7 at size 6" in message
        assert "ran 1 time at size 1, 6 times at size 6" in message
        assert "in _names_in_a_loop" in message

    asyncio.run(main())


def test_async_accepts_plain_functions() -> None:
    engine = _engine()

    def fn(size: int) -> None:
        with engine.connect() as conn:
            conn.execute(text("select 1"))

    async def main() -> dict[int, QueryLog]:
        return await assert_queries_do_not_grow_async(fn, setup=lambda size: size)

    logs = asyncio.run(main())
    assert [log.count for log in logs.values()] == [1, 1]


def test_in_a_pytest_run_the_failure_points_at_the_test(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(
        test_rows="""
        from sqlalchemy import create_engine, text
        from pytest_queryguard import assert_queries_do_not_grow

        engine = create_engine("sqlite://")


        def per_row(size):
            with engine.connect() as conn:
                for _ in range(size):
                    conn.execute(text("select 1"))


        def test_rows():
            assert_queries_do_not_grow(per_row, sizes=(1, 3))
        """
    )
    result = pytester.runpytest("--tb=short", "-p", "no:randomly")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "test_rows.py:*: in test_rows",
            "    assert_queries_do_not_grow(per_row, sizes=(1, 3))",
            (
                "E   pytest_queryguard.QueryBudgetExceeded: queryguard: the number "
                "of statements grows with the size: 1 at size 1, 3 at size 3"
            ),
            "E     select 1",
            "E     ran 1 time at size 1, 3 times at size 3",
            "E     first at test_rows.py:10 in per_row",
            "E       called from test_rows.py:14 in test_rows",
        ]
    )
    assert "scaling.py" not in result.stdout.str()
