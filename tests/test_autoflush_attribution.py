"""A lazy load that autoflushes pending changes first keeps its attribution."""

from __future__ import annotations

import pytest
from sqlalchemy import ForeignKey, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from pytest_queryguard import record_queries

pytestmark = pytest.mark.allow_nplusone


class Base(DeclarativeBase):
    pass


class Parent(Base):
    __tablename__ = "parent"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(default="")
    children: Mapped[list[Child]] = relationship()


class Child(Base):
    __tablename__ = "child"
    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("parent.id"))


def test_the_autoflush_before_a_lazy_load_does_not_take_its_relationship() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(Parent(id=i, children=[Child()]) for i in range(1, 4))
        session.commit()
    with Session(engine) as session, record_queries() as log:
        for parent in session.scalars(select(Parent)).all():
            parent.name = "changed"  # pending UPDATE, flushed by the next load
            parent.children  # noqa: B018  # the lazy load
    by_verb = {}
    for query in log:
        by_verb.setdefault(query.sql.split(None, 1)[0], set()).add(query.relationship)
    assert by_verb["UPDATE"] == {None}
    assert by_verb["SELECT"] == {None, "Parent.children"}
    loads = [q for q in log if "child" in q.sql and q.sql.startswith("SELECT")]
    assert len(loads) == 3
    assert {q.relationship for q in loads} == {"Parent.children"}
    # With the lazy loads attributed, they are not also counted as a loop of one
    # statement: only the relationship rule has something to say.
    with pytest.raises(AssertionError) as raised:
        log.assert_no_nplusone(repeat_threshold=3)
    assert "same statement" not in str(raised.value)
