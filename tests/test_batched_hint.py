"""A statement that already uses ``IN (...)`` gets a hint that fits it."""

from __future__ import annotations

import pytest

# pytester runs sessions in-process, so the outer plugin sees their queries too.
pytestmark = pytest.mark.allow_nplusone

BATCHED = """
from sqlalchemy import create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class Base(DeclarativeBase):
    pass


class Org(Base):
    __tablename__ = "orgs"
    id: Mapped[int] = mapped_column(primary_key=True)


def load_many(engine, ids):
    with Session(engine) as s:
        return s.scalars(select(Org).where(Org.id.in_(ids))).all()


def load_one(engine, org_id):
    with Session(engine) as s:
        return s.scalars(select(Org).where(Org.id == org_id)).one()


def make_engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add_all([Org(id=i) for i in range(1, 11)])
        s.commit()
    return engine


def test_batched_lookup_called_five_times():
    engine = make_engine()
    for k in range(5):
        load_many(engine, [k, k + 1, k + 2])


def test_single_lookup_called_five_times():
    engine = make_engine()
    for k in range(1, 6):
        load_one(engine, k)
"""


def test_the_hint_depends_on_whether_the_statement_uses_in(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(test_batched=BATCHED)
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "--tb=short")
    result.assert_outcomes(failed=2)
    out = result.stdout.str()
    assert out.count("already fetches several rows with IN (...)") == 1
    assert out.count("fix: fetch all rows in one query (WHERE id IN (...))") == 1
