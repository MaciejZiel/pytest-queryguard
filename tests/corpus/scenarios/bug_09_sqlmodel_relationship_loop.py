"""BUG relationship-load: a SQLModel ``Relationship`` read for every row.

``hero.team`` is a lazy many-to-one; listing heroes with their team name runs a
SELECT per team.
"""

from sqlmodel import Field, Relationship, Session, SQLModel, select

from ..kit import BUG, REL, Ctx

VERDICT = BUG
KINDS = frozenset({REL})


class BugTeam(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    heroes: list["BugHero"] = Relationship(back_populates="team")


class BugHero(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    team_id: int | None = Field(default=None, foreign_key="bugteam.id", index=True)
    team: BugTeam | None = Relationship(back_populates="heroes")


def setup(ctx: Ctx) -> None:
    ctx.reset()
    SQLModel.metadata.create_all(ctx.engine())
    with Session(ctx.engine()) as session:
        for t in range(1, 9):
            team = BugTeam(id=t, name=f"team{t}")
            session.add(team)
            session.add_all(BugHero(name=f"hero{t}{k}", team=team) for k in range(2))
        session.commit()


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        rows = [(h.name, h.team.name) for h in session.exec(select(BugHero)) if h.team]
    assert len(rows) == 16
