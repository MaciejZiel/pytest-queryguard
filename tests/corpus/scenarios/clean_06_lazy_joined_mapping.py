"""CLEAN: ``relationship(lazy="joined")`` on a many-to-one mapping."""

from sqlalchemy import ForeignKey, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()


class Base(DeclarativeBase):
    pass


class Team(Base):
    __tablename__ = "team"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]


class Player(Base):
    __tablename__ = "player"
    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("team.id"))
    team: Mapped[Team] = relationship(lazy="joined")


def setup(ctx: Ctx) -> None:
    ctx.reset()
    Base.metadata.create_all(ctx.engine())
    with Session(ctx.engine()) as session:
        teams = [Team(id=t, name=f"team{t}") for t in range(1, 7)]
        session.add_all(Player(team=teams[i % 6]) for i in range(18))
        session.commit()


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        names = [p.team.name for p in session.scalars(select(Player))]
    assert len(names) == 18
