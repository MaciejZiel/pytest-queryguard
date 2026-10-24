"""Fixtures of the in-app mode tests: models, seeded engines and a clean state."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import ForeignKey, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncEngine, create_async_engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
)

from pytest_queryguard.app import _core, _listeners

HERE = Path(__file__).parent

CUSTOMERS = 5


class Base(AsyncAttrs, DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    bio: Mapped[str] = mapped_column(deferred=True, default="")
    orders: Mapped[list[Order]] = relationship(back_populates="customer")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    customer: Mapped[Customer] = relationship(back_populates="orders")


def seed(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for c in range(1, CUSTOMERS + 1):
            customer = Customer(id=c, name=f"customer {c}", bio=f"bio {c}")
            customer.orders = [Order(id=c * 10 + o) for o in range(2)]
            session.add(customer)
        session.commit()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    # These tests run N+1 code on purpose; the outer plugin must not fail them.
    for item in items:
        if HERE in item.path.parents:
            item.add_marker(pytest.mark.allow_nplusone)


@pytest.fixture(autouse=True)
def clean_state() -> Iterator[None]:
    _core._reset_for_tests()
    yield
    for engine in list(_listeners._watched):
        _listeners.unwatch(engine)
    _core._reset_for_tests()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "inapp.sqlite"


@pytest.fixture
def engine(db_path: Path) -> Iterator[Engine]:
    engine = create_engine(f"sqlite:///{db_path}")
    seed(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def watched(engine: Engine) -> Engine:
    _listeners.watch(engine)
    return engine


@pytest.fixture
def async_engine(engine: Engine, db_path: Path) -> Iterator[AsyncEngine]:
    # Seeded through the sync engine fixture, on the same file.
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    yield async_engine
    asyncio.run(async_engine.dispose())
