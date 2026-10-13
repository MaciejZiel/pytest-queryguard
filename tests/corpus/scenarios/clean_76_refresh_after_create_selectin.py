"""CLEAN: two new rows, each refreshed by name, with a ``lazy="selectin"`` collection.

A repository's "create" refreshes the new row after the flush. SQLAlchemy loads
the mapped selectin collection for that one object during the refresh. That is the strategy the mapping asks for, not a
lazy load, and a ``selectinload`` option would change nothing.
"""

from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()


class Base(DeclarativeBase):
    pass


class Parent(Base):
    __tablename__ = "parent"
    id: Mapped[int] = mapped_column(primary_key=True)
    children: Mapped[list["Child"]] = relationship(lazy="selectin")


class Child(Base):
    __tablename__ = "child"
    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("parent.id"))


def setup(ctx: Ctx) -> None:
    ctx.reset()
    Base.metadata.create_all(ctx.engine())


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        parents = [Parent(id=1), Parent(id=2)]
        session.add_all(parents)
        session.flush()
        for parent in parents:
            session.refresh(parent, ["children"])
    assert [p.children for p in parents] == [[], []]
