"""BUG column-load: joined-table inheritance, subclass columns read per instance.

``select(Employee)`` loads the base table only; reading ``budget`` or
``language`` of each instance loads the subclass table with a SELECT per object.
``with_polymorphic`` would fetch them in the first query.
"""

from sqlalchemy import ForeignKey, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from ..kit import BUG, COLUMN, Ctx

VERDICT = BUG
KINDS = frozenset({COLUMN})


class Base(DeclarativeBase):
    pass


class Employee(Base):
    __tablename__ = "employee"
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str]
    name: Mapped[str]
    __mapper_args__ = {"polymorphic_on": "type", "polymorphic_identity": "employee"}


class Manager(Employee):
    __tablename__ = "manager"
    id: Mapped[int] = mapped_column(ForeignKey("employee.id"), primary_key=True)
    budget: Mapped[int]
    __mapper_args__ = {"polymorphic_identity": "manager"}


class Engineer(Employee):
    __tablename__ = "engineer"
    id: Mapped[int] = mapped_column(ForeignKey("employee.id"), primary_key=True)
    language: Mapped[str]
    __mapper_args__ = {"polymorphic_identity": "engineer"}


def setup(ctx: Ctx) -> None:
    ctx.reset()
    Base.metadata.create_all(ctx.engine())
    with Session(ctx.engine()) as session:
        for i in range(1, 7):
            session.add(Manager(id=i, name=f"m{i}", budget=i * 1000))
        for i in range(7, 13):
            session.add(Engineer(id=i, name=f"e{i}", language="python"))
        session.commit()


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        details = [
            e.budget if isinstance(e, Manager) else e.language  # type: ignore[attr-defined]
            for e in session.scalars(select(Employee))
        ]
    assert len(details) == 12
