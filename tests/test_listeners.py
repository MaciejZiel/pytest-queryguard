from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pytest_queryguard import collector, listeners
from tests.models import Customer, Order, make_sync_session


@pytest.fixture(autouse=True)
def _isolated() -> Iterator[None]:
    listeners.install()
    collector._reset_for_tests()
    listeners._reset_for_tests()
    yield
    collector._reset_for_tests()
    listeners._reset_for_tests()


@pytest.fixture
def session() -> Iterator[Session]:
    s = make_sync_session()
    yield s
    s.close()


@pytest.fixture
def rec() -> Iterator[collector.QueryCollector]:
    c = collector.QueryCollector()
    collector.push(c)
    yield c
    collector.pop(c)


def test_nothing_recorded_without_active_collector(session: Session) -> None:
    c = collector.QueryCollector()
    for order in session.scalars(select(Order)):
        _ = order.customer
    assert c.statements == []
    assert c.relationship_loads == []
    assert collector.active() == ()


def test_lazy_load_recorded_with_relationship_name(
    session: Session, rec: collector.QueryCollector
) -> None:
    for order in session.scalars(select(Order)).all():
        _ = order.customer
    assert [e.relationship for e in rec.relationship_loads] == ["Order.customer"] * 3
    assert len({e.parent_key for e in rec.relationship_loads}) == 3
    assert rec.relationship_loads[0].callsite is not None


def test_statement_tagged_with_relationship(
    session: Session, rec: collector.QueryCollector
) -> None:
    order = session.scalars(select(Order)).first()
    assert order is not None
    _ = order.customer
    tagged = [s for s in rec.statements if s.relationship == "Order.customer"]
    assert len(tagged) == 1
    assert "customers" in tagged[0].sql.lower()


def test_pending_relationship_does_not_leak(
    session: Session, rec: collector.QueryCollector
) -> None:
    session.execute(select(Customer).where(Customer.id == 1)).scalar_one()
    orders = session.scalars(select(Order).where(Order.customer_id == 1)).all()
    assert len(orders) == 2
    for order in orders:
        _ = order.customer  # served from the identity map
    session.execute(select(Customer))
    assert rec.statements[-1].relationship is None


def test_nested_collectors_both_receive_events(session: Session) -> None:
    outer, inner = collector.QueryCollector(), collector.QueryCollector()
    collector.push(outer)
    collector.push(inner)
    try:
        session.execute(select(Customer)).all()
    finally:
        collector.pop(inner)
        collector.pop(outer)
    assert len(outer.statements) == 1
    assert len(inner.statements) == 1


def test_listener_error_is_swallowed_and_disables(
    session: Session,
    rec: collector.QueryCollector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom() -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(listeners, "find_callsite", boom)
    assert listeners.internal_error() is None
    assert len(session.execute(select(Customer)).all()) == 3
    assert isinstance(listeners.internal_error(), RuntimeError)
    monkeypatch.undo()
    before = len(rec.statements)
    session.execute(select(Customer)).all()
    assert len(rec.statements) == before
