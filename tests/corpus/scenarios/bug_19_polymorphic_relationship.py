"""BUG relationship-load: a relationship defined on a joined-table subclass.

``Manager.reports`` is lazy; the org-chart loop reads it for every manager.
"""

from sqlalchemy import ForeignKey, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from ..kit import BUG, REL, Ctx

VERDICT = BUG
KINDS = frozenset({REL})


class Base(DeclarativeBase):
    pass


class Employee(Base):
    __tablename__ = "employee"
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str]
    name: Mapped[str]
    manager_id: Mapped[int | None] = mapped_column(ForeignKey("manager.id"))
    __mapper_args__ = {"polymorphic_on": "type", "polymorphic_identity": "employee"}


class Manager(Employee):
    __tablename__ = "manager"
    id: Mapped[int] = mapped_column(ForeignKey("employee.id"), primary_key=True)
    budget: Mapped[int] = mapped_column(default=0)
    reports: Mapped[list[Employee]] = relationship(
        foreign_keys=[Employee.manager_id], primaryjoin="Employee.manager_id == Manager.id"
    )
    __mapper_args__ = {"polymorphic_identity": "manager", "inherit_condition": id == Employee.id}


def setup(ctx: Ctx) -> None:
    ctx.reset()
    Base.metadata.create_all(ctx.engine())
    with Session(ctx.engine()) as session:
        managers = [Manager(id=i, name=f"m{i}", budget=i) for i in range(1, 7)]
        session.add_all(managers)
        session.flush()
        for i in range(7, 19):
            session.add(Employee(id=i, name=f"e{i}", manager_id=1 + i % 6))
        session.commit()


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        chart = {m.name: len(m.reports) for m in session.scalars(select(Manager))}
    assert len(chart) == 6
