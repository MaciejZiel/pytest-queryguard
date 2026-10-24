"""BUG column-load: one bulk insert, then a refresh for every new row.

``add_all()`` and one flush write the eight rows together; refreshing them one
by one afterwards is a SELECT by primary key per row, where one query with
``populate_existing`` (or ``eager_defaults``) would read them all back.
"""

from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from ..kit import BUG, COLUMN, Ctx

VERDICT = BUG
KINDS = frozenset({COLUMN})


class Base(DeclarativeBase):
    pass


class Invoice(Base):
    __tablename__ = "invoice"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str]
    status: Mapped[str] = mapped_column(server_default="draft")


def setup(ctx: Ctx) -> None:
    ctx.reset()
    Base.metadata.create_all(ctx.engine())


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        invoices = [Invoice(number=f"INV-{i}") for i in range(8)]
        session.add_all(invoices)
        session.flush()
        for invoice in invoices:
            session.refresh(invoice)
        session.commit()
        statuses = {invoice.status for invoice in invoices}
    assert statuses == {"draft"}
