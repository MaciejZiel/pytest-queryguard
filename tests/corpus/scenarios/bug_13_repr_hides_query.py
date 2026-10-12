"""BUG relationship-load: ``__repr__`` reads a relationship and a log line calls it.

A debugging ``__repr__`` that includes ``self.team.name`` is formatted for every
player by an f-string in a logging loop.
"""

from sqlalchemy import ForeignKey, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from ..kit import BUG, REL, Ctx

VERDICT = BUG
KINDS = frozenset({REL})


class Base(DeclarativeBase):
    pass


class Team(Base):
    __tablename__ = "team"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]


class Player(Base):
    __tablename__ = "player"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    team_id: Mapped[int] = mapped_column(ForeignKey("team.id"))
    team: Mapped[Team] = relationship()

    def __repr__(self) -> str:
        return f"<Player {self.name} of {self.team.name}>"


def setup(ctx: Ctx) -> None:
    ctx.reset()
    Base.metadata.create_all(ctx.engine())
    with Session(ctx.engine()) as session:
        teams = [Team(id=t, name=f"team{t}") for t in range(1, 7)]
        session.add_all(teams)
        session.add_all(Player(name=f"p{i}", team=teams[i % 6]) for i in range(18))
        session.commit()


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        lines = [f"loaded {p!r}" for p in session.scalars(select(Player))]
    assert len(lines) == 18
