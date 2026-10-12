from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pytest_queryguard import collector, listeners, plugin
from tests.models import Customer, Order, make_sync_session


@pytest.fixture(autouse=True)
def _isolated(request: pytest.FixtureRequest) -> Iterator[None]:
    # These tests drive the listeners by hand: switch the plugin's own
    # measurement of this session off (it is read when the test body starts).
    stash = request.config.stash
    saved = stash[plugin._settings]
    stash[plugin._settings] = plugin._Settings("off", 2, 5)
    listeners.install()
    collector._reset_for_tests()
    listeners._reset_for_tests()
    yield
    collector._reset_for_tests()
    listeners._reset_for_tests()
    stash[plugin._settings] = saved


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


def test_nothing_recorded_without_active_collector(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom() -> None:
        raise RuntimeError("must not be called without a collector")

    monkeypatch.setattr(listeners, "find_callsite", boom)
    for order in session.scalars(select(Order)).all():
        _ = order.customer
    assert listeners.internal_error() is None
    assert collector.active() == ()
    monkeypatch.undo()

    c = collector.QueryCollector()
    collector.push(c)
    try:
        session.execute(select(Customer)).all()
    finally:
        collector.pop(c)
    assert len(c.statements) == 1


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
    orders = session.scalars(select(Order).where(Order.customer_id == 1)).all()
    assert len(orders) == 2
    first_customer = orders[0].customer  # real lazy load
    assert len(rec.relationship_loads) == 1
    assert orders[1].customer is first_customer  # identity map, no load
    assert len(rec.relationship_loads) == 1
    session.execute(select(Customer))
    assert rec.statements[-1].relationship is None


def test_stale_pending_tag_does_not_cross_measurements(
    session: Session,
) -> None:
    first = collector.QueryCollector()
    collector.push(first)
    listeners._set_pending("Order.customer")
    collector.pop(first)

    second = collector.QueryCollector()
    collector.push(second)
    try:
        session.execute(select(Customer)).all()
    finally:
        collector.pop(second)
    assert second.statements[-1].relationship is None


def test_pop_removes_by_identity_not_equality(session: Session) -> None:
    outer, inner = collector.QueryCollector(), collector.QueryCollector()
    collector.push(outer)
    collector.push(inner)
    collector.pop(inner)
    try:
        session.execute(select(Customer)).all()
    finally:
        collector.pop(outer)
    assert len(outer.statements) == 1
    assert inner.statements == []


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


@pytest.mark.filterwarnings(
    "ignore::pytest_queryguard.warnings.QueryGuardInternalWarning"
)
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


def test_a_column_load_without_an_instance_is_ignored_quietly() -> None:
    class State:
        is_column_load = True
        load_options = object()  # no _refresh_state: an unknown SQLAlchemy shape

    target = collector.QueryCollector()
    collector.push(target)
    try:
        listeners._on_orm_execute(State())  # type: ignore[arg-type]
    finally:
        collector.pop(target)
    assert listeners.internal_error() is None
    assert target.column_loads == []
    assert listeners._take_pending() == (None, None)


@pytest.mark.filterwarnings(
    "ignore::pytest_queryguard.warnings.QueryGuardInternalWarning"
)
def test_missing_only_load_props_is_an_internal_error_not_a_refresh(
    session: Session, rec: collector.QueryCollector
) -> None:
    from types import SimpleNamespace

    from sqlalchemy import inspect

    customer = session.scalars(select(Customer)).first()
    assert customer is not None
    # the shape of a SQLAlchemy release that drops _only_load_props
    state = SimpleNamespace(
        is_column_load=True,
        load_options=SimpleNamespace(_refresh_state=inspect(customer)),
        statement=SimpleNamespace(_compile_options=SimpleNamespace()),
    )
    listeners._on_orm_execute(state)  # type: ignore[arg-type]
    error = listeners.internal_error()
    assert isinstance(error, AttributeError)
    assert "_only_load_props" in str(error)
    failure = listeners.internal_failure()
    assert failure is not None and "do_orm_execute" in failure.source
    assert rec.column_loads == []  # no load was mislabeled as a refresh


def test_the_present_attribute_is_classified_as_before(
    session: Session, rec: collector.QueryCollector
) -> None:
    customer = session.scalars(select(Customer)).first()
    assert customer is not None
    session.expire(customer)
    assert customer.name
    assert listeners.internal_error() is None
    [load] = rec.column_loads
    assert load.cause == "expired"
