"""CLEAN: SQLModel with ``selectinload(Hero.team)`` options on the statement."""

from sqlalchemy.orm import selectinload
from sqlmodel import Field, Relationship, Session, SQLModel, select

from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
REQUIRES = ("sqlmodel",)


class OkTeam(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    heroes: list["OkHero"] = Relationship(back_populates="team")


class OkHero(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    team_id: int | None = Field(default=None, foreign_key="okteam.id", index=True)
    team: OkTeam | None = Relationship(back_populates="heroes")


def setup(ctx: Ctx) -> None:
    ctx.reset()
    SQLModel.metadata.create_all(ctx.engine())
    with Session(ctx.engine()) as session:
        for t in range(1, 9):
            team = OkTeam(id=t, name=f"team{t}")
            session.add(team)
            session.add_all(OkHero(name=f"hero{t}{k}", team=team) for k in range(2))
        session.commit()


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        stmt = select(OkHero).options(selectinload(OkHero.team))  # type: ignore[arg-type]
        rows = [(h.name, h.team.name) for h in session.exec(stmt) if h.team]
    assert len(rows) == 16
