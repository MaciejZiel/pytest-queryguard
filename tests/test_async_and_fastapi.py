from __future__ import annotations

from pathlib import Path

import pytest

# pytester runs sessions in-process, so the outer plugin sees their queries too.
pytestmark = pytest.mark.allow_nplusone

MODELS = (Path(__file__).parent / "models.py").read_text()

ASYNC_SETUP = """
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import StaticPool

from qgmodels import Base, Customer, Order


async def make_session() -> AsyncSession:
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    for c in range(1, 4):
        customer = Customer(id=c, name=f"customer {c}")
        customer.orders = [Order(id=c * 10 + o) for o in range(2)]
        session.add(customer)
    await session.commit()
    session.expunge_all()
    return session
"""

# AsyncAttrs must be on the mapped classes, so the async scenarios use their own models.
ASYNC_MODELS = """
from sqlalchemy import ForeignKey
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(AsyncAttrs, DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    orders: Mapped[list["Order"]] = relationship(back_populates="customer")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    customer: Mapped[Customer] = relationship(back_populates="orders")
"""


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makepyfile(qgmodels=MODELS, qgasync=ASYNC_MODELS)
    return pytester


def _async_scenario(project: pytest.Pytester, body: str) -> None:
    setup = ASYNC_SETUP.replace("from qgmodels import", "from qgasync import")
    project.makepyfile(test_a=setup + "\n\n" + body)


def test_async_session_get_in_loop_fails(project: pytest.Pytester) -> None:
    _async_scenario(
        project,
        """
def test_loop():
    async def main():
        session = await make_session()
        for i in range(5):
            await session.get(Customer, i + 1)

    asyncio.run(main())
""",
    )
    result = project.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        ["*N+1 detected*", "*the same statement ran *", "*first at *test_a.py:*"]
    )


def test_async_in_query_passes(project: pytest.Pytester) -> None:
    _async_scenario(
        project,
        """
def test_ok():
    async def main():
        session = await make_session()
        rows = (await session.scalars(
            select(Customer).where(Customer.id.in_([1, 2, 3]))
        )).all()
        assert len(rows) == 3

    asyncio.run(main())
""",
    )
    project.runpytest().assert_outcomes(passed=1)


def test_async_awaitable_attrs_relationship_fails(project: pytest.Pytester) -> None:
    _async_scenario(
        project,
        """
def test_loop():
    async def main():
        session = await make_session()
        orders = (await session.scalars(select(Order))).all()
        for order in orders:
            customer = await order.awaitable_attrs.customer
            assert customer.name

    asyncio.run(main())
""",
    )
    result = project.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "*N+1 detected*",
            "*Order.customer was lazy-loaded*",
            "*first at *test_a.py:*",
        ]
    )


def test_async_selectinload_passes(project: pytest.Pytester) -> None:
    _async_scenario(
        project,
        """
def test_ok():
    async def main():
        session = await make_session()
        stmt = select(Order).options(selectinload(Order.customer))
        for order in (await session.scalars(stmt)).all():
            customer = await order.awaitable_attrs.customer
            assert customer.name

    asyncio.run(main())
""",
    )
    project.runpytest().assert_outcomes(passed=1)


APP = """
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from qgmodels import Order, make_sync_session

app = FastAPI()
session = make_sync_session()


@app.get("/orders")
def orders_nplusone():
    result = []
    for order in session.query(Order).all():
        result.append(order.customer.name)
    return result


@app.get("/fixed")
def orders_fixed():
    stmt = select(Order).options(selectinload(Order.customer))
    return [o.customer.name for o in session.scalars(stmt)]
"""


def test_fastapi_testclient_endpoint_nplusone_fails(project: pytest.Pytester) -> None:
    project.makepyfile(
        app_module=APP,
        test_a="""
from fastapi.testclient import TestClient

from app_module import app


def test_endpoint():
    assert TestClient(app).get("/orders").status_code == 200
""",
    )
    result = project.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "*N+1 detected*",
            "*Order.customer was lazy-loaded*",
            "*first at *app_module.py:*",
        ]
    )


def test_fastapi_fixed_endpoint_passes(project: pytest.Pytester) -> None:
    project.makepyfile(
        app_module=APP,
        test_a="""
from fastapi.testclient import TestClient

from app_module import app


def test_endpoint():
    assert TestClient(app).get("/fixed").status_code == 200
""",
    )
    project.runpytest().assert_outcomes(passed=1)


def test_plain_thread_queries_are_collected(project: pytest.Pytester) -> None:
    project.makepyfile(
        test_a="""
import threading

from qgmodels import Order, make_sync_session


def test_thread():
    session = make_sync_session()

    def work():
        for order in session.query(Order).all():
            _ = order.customer.name

    thread = threading.Thread(target=work)
    thread.start()
    thread.join()
"""
    )
    result = project.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        ["*Order.customer was lazy-loaded*", "*first at *test_a.py:*"]
    )


def test_plain_thread_fixed_passes(project: pytest.Pytester) -> None:
    project.makepyfile(
        test_a="""
import threading

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from qgmodels import Order, make_sync_session


def test_thread():
    session = make_sync_session()

    def work():
        stmt = select(Order).options(selectinload(Order.customer))
        for order in session.scalars(stmt):
            _ = order.customer.name

    thread = threading.Thread(target=work)
    thread.start()
    thread.join()
"""
    )
    project.runpytest().assert_outcomes(passed=1)
