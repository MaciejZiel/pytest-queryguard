"""CLEAN: a repository that refreshes every row it creates or updates.

Each ``create()`` adds one row, flushes and refreshes it to read the server's
defaults (advanced-alchemy's ``auto_refresh``); each ``update()`` does the same
after the change. Eight creates and three updates in one test are eleven
refreshes, each of the object the flush before it wrote: the caller asked for
each one, and there is no loop of reads to batch.
"""

from sqlalchemy import ForeignKey, func
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
KNOWN = {
    "write-loop": "eight creates in a loop are the opt-in write-loop rule's pattern",
    "all-opt-in": "eight creates in a loop are the opt-in write-loop rule's pattern",
}


class Base(DeclarativeBase):
    pass


class Team(Base):
    __tablename__ = "team"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    version: Mapped[int] = mapped_column(server_default="1")
    members: Mapped[list["Member"]] = relationship(lazy="selectin")


class Member(Base):
    __tablename__ = "member"
    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("team.id"))


def setup(ctx: Ctx) -> None:
    ctx.reset()
    Base.metadata.create_all(ctx.engine())


def create(session: Session, name: str) -> Team:
    team = Team(name=name)
    session.add(team)
    session.flush()
    session.refresh(team)
    return team


def update(session: Session, team: Team, name: str) -> Team:
    team.name = name
    session.flush()
    session.refresh(team)
    return team


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        teams = [create(session, f"team {i}") for i in range(8)]
        for team in teams[:3]:
            update(session, team, team.name + " renamed")
        versions = {team.version for team in teams}
        session.commit()
        total = session.scalar(func.count(Team.id))
    assert total == 8
    assert versions == {1}
