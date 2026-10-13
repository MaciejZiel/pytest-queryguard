"""``session.refresh()`` of an object right after the flush that wrote it.

A repository layer (advanced-alchemy's ``auto_refresh``, litestar-fullstack's
services) refreshes each object it creates or updates, to read server defaults
and onupdate values. That refresh is asked for once per write; it is not a
loop of reads, so it is not a column-load finding, whatever the number of
creates in a test. A loop of refreshes of objects that were only read, a
refresh of every row of one bulk insert, and a second refresh of the same
write are still counted.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import ForeignKey, create_engine, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship
from sqlalchemy.pool import StaticPool

from pytest_queryguard import NPlusOneDetected, record_queries

pytestmark = pytest.mark.allow_nplusone

ROWS = 8


class Base(DeclarativeBase):
    pass


class Team(Base):
    __tablename__ = "team"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    version: Mapped[int] = mapped_column(server_default="1")
    members: Mapped[list[Member]] = relationship(lazy="selectin")


class Member(Base):
    __tablename__ = "member"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("team.id"))


@pytest.fixture
def engine() -> Iterator[Any]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def _report(log: Any, **thresholds: int) -> str:
    try:
        log.assert_no_nplusone(**thresholds)
    except NPlusOneDetected as exc:
        return str(exc)
    return ""


def _create(session: Session, name: str, *, commit: bool = False) -> Team:
    team = Team(name=name)
    session.add(team)
    if commit:
        session.commit()
    else:
        session.flush()
    session.refresh(team)
    return team


@pytest.mark.parametrize("commit", [False, True], ids=["flush", "commit"])
def test_creates_each_refreshed_are_clean(engine: Any, commit: bool) -> None:
    with Session(engine) as session, record_queries() as log:
        for i in range(ROWS):
            _create(session, f"t{i}", commit=commit)
    assert _report(log) == ""
    # also with the lowest column-load threshold
    assert _report(log, column_load_threshold=2) == ""


def test_the_refreshes_are_not_a_repeated_statement_either(engine: Any) -> None:
    with Session(engine) as session, record_queries() as log:
        for i in range(ROWS):
            _create(session, f"t{i}")
    assert _report(log, repeat_threshold=2) == ""


def test_creates_refreshed_by_attribute_name_are_clean(engine: Any) -> None:
    with Session(engine) as session, record_queries() as log:
        for i in range(ROWS):
            team = Team(name=f"t{i}")
            session.add(team)
            session.commit()
            session.refresh(team, ["members"])
    assert _report(log, column_load_threshold=2) == ""


def test_updates_each_refreshed_are_clean(engine: Any) -> None:
    with Session(engine) as session:
        teams = [_create(session, f"t{i}") for i in range(ROWS)]
        with record_queries() as log:
            for team in teams:
                team.name += " renamed"
                session.flush()
                session.refresh(team)
    assert _report(log, column_load_threshold=2) == ""


def test_a_refresh_loop_of_objects_that_were_only_read_is_reported(
    engine: Any,
) -> None:
    with Session(engine) as session:
        for i in range(ROWS):
            _create(session, f"t{i}")
        session.commit()
    with Session(engine) as session, record_queries() as log:
        for team in session.scalars(select(Team)).all():
            session.refresh(team)
    report = _report(log)
    assert f"Team was reloaded with session.refresh() {ROWS} times" in report


def test_refreshing_each_row_of_a_bulk_insert_is_reported(engine: Any) -> None:
    with Session(engine) as session, record_queries() as log:
        teams = [Team(name=f"t{i}") for i in range(ROWS)]
        session.add_all(teams)
        session.flush()
        for team in teams:
            session.refresh(team)
    # one refresh per class and flush is let through, the others count
    report = _report(log)
    assert f"reloaded with session.refresh() {ROWS - 1} times" in report


def test_a_second_refresh_of_the_same_write_counts(engine: Any) -> None:
    with Session(engine) as session, record_queries() as log:
        for i in range(2):
            team = _create(session, f"t{i}")
            session.refresh(team)
    report = _report(log, column_load_threshold=2)
    assert "reloaded with session.refresh() 2 times for 2 instances" in report


def test_a_refresh_after_a_later_flush_counts(engine: Any) -> None:
    # only the latest flush of the Session is a write the refresh reads back
    with Session(engine) as session, record_queries() as log:
        a, b = Team(name="a"), Team(name="b")
        session.add(a)
        session.flush()
        session.add(b)
        session.flush()
        session.refresh(b)  # let through: the latest flush wrote it
        session.refresh(a)
        c = Team(name="c")
        session.add(c)
        session.flush()
        session.refresh(b)
    report = _report(log, column_load_threshold=2)
    assert "reloaded with session.refresh() 2 times for 2 instances" in report


def test_async_creates_each_refreshed_are_clean() -> None:
    async def main() -> Any:
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            with record_queries() as log:
                async with AsyncSession(engine) as session:
                    for i in range(ROWS):
                        team = Team(name=f"t{i}")
                        session.add(team)
                        await session.commit()
                        await session.refresh(team)
            return log
        finally:
            await engine.dispose()

    assert _report(asyncio.run(main()), column_load_threshold=2) == ""


def test_a_refresh_loop_after_flushes_that_wrote_nothing_is_reported(
    engine: Any,
) -> None:
    # Assigning an attribute its own value puts the object in session.dirty,
    # but the flush emits no UPDATE: the refresh reads back no write.
    with Session(engine) as session:
        for i in range(ROWS):
            _create(session, f"t{i}")
        session.commit()
    with Session(engine) as session, record_queries() as log:
        for team in session.scalars(select(Team)).all():
            team.name = team.name
            session.flush()
            session.refresh(team)
    assert not any(q.sql.startswith("UPDATE") for q in log.queries)
    report = _report(log)
    assert f"Team was reloaded with session.refresh() {ROWS} times" in report
