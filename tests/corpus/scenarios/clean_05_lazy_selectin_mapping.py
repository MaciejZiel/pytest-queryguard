"""CLEAN: ``relationship(lazy="selectin")`` on the mapping.

The default loader strategy is configured once on the model, so plain
``select(Parent)`` queries load the children with one extra statement.
"""

from sqlalchemy import ForeignKey, select
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
    with Session(ctx.engine()) as session:
        session.add_all(Parent(id=i, children=[Child(), Child()]) for i in range(1, 9))
        session.commit()


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        counts = [len(p.children) for p in session.scalars(select(Parent))]
    assert counts == [2] * 8
