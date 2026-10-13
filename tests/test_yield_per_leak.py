"""``yield_per`` with ``selectinload`` keeps working with the plugin installed.

On SQLAlchemy 2.0.20 to 2.0.49 any ``do_orm_execute`` listener makes the outer
statement's ``yield_per`` leak into its ``selectinload`` statement (SQLAlchemy
issue 13301, fixed in 2.0.50). The plugin installs such a listener, so it
removes the leaked option again; see issue 169.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import sqlalchemy
from sqlalchemy import ForeignKey, create_engine, event, select
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    selectinload,
)

from pytest_queryguard import listeners


class Base(DeclarativeBase):
    pass


class Parent(Base):
    __tablename__ = "yp_parent"
    id: Mapped[int] = mapped_column(primary_key=True)
    lines: Mapped[list[Line]] = relationship()


class Line(Base):
    __tablename__ = "yp_line"
    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("yp_parent.id"))


@pytest.fixture
def session() -> Iterator[Session]:
    listeners.install()
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add_all([Parent(id=i, lines=[Line()]) for i in range(1, 1201)])
        s.commit()
    with Session(engine) as s:
        yield s
    engine.dispose()


def _stream(session: Session, batch: int = 200) -> int:
    stmt = (
        select(Parent)
        .options(selectinload(Parent.lines))
        .execution_options(yield_per=batch)
    )
    return sum(len(p.lines) for p in session.scalars(stmt))


def test_yield_per_with_selectinload_works_with_the_listener(session: Session) -> None:
    assert _stream(session) == 1200


def test_yield_per_method_with_selectinload_works(session: Session) -> None:
    stmt = select(Parent).options(selectinload(Parent.lines))
    assert sum(len(p.lines) for p in session.scalars(stmt).yield_per(300)) == 1200


def test_the_outer_statement_still_streams_in_batches(session: Session) -> None:
    sizes: list[int] = []
    stmt = (
        select(Parent)
        .options(selectinload(Parent.lines))
        .execution_options(yield_per=200)
    )
    for chunk in session.scalars(stmt).partitions():
        sizes.append(len(chunk))
    assert sizes == [200] * 6


def test_a_listener_of_the_user_is_healed_too(session: Session) -> None:
    def noop(state: object) -> None:
        pass

    event.listen(Session, "do_orm_execute", noop)
    try:
        assert _stream(session) == 1200
    finally:
        event.remove(Session, "do_orm_execute", noop)


def test_the_heal_listener_is_registered_only_where_needed() -> None:
    listeners.install()
    registered = event.contains(
        Session, "do_orm_execute", listeners._on_orm_execute_heal
    )
    assert registered == listeners._leaks_yield_per()


@pytest.mark.parametrize(
    ("version", "affected"),
    [
        ("2.0.13", False),
        ("2.0.19", False),
        ("2.0.20", True),
        ("2.0.49", True),
        ("2.0.50", False),
        ("2.0.54", False),
        ("2.1.0", False),
        ("2.1.0b1", False),
        ("2.0.20rc1", True),
        ("garbage", False),
    ],
)
def test_affected_versions(
    monkeypatch: pytest.MonkeyPatch, version: str, affected: bool
) -> None:
    monkeypatch.setattr(sqlalchemy, "__version__", version)
    assert listeners._leaks_yield_per() is affected


@pytest.mark.skipif(
    not listeners._leaks_yield_per(), reason="SQLAlchemy does not leak yield_per"
)
def test_without_the_heal_listener_sqlalchemy_still_raises(session: Session) -> None:
    # Proves the tests above exercise the bug: remove the heal listener and the
    # leak is back.
    event.remove(Session, "do_orm_execute", listeners._on_orm_execute_heal)
    try:
        with pytest.raises(sqlalchemy.exc.InvalidRequestError, match="unique"):
            _stream(session)
    finally:
        event.listen(Session, "do_orm_execute", listeners._on_orm_execute_heal)
