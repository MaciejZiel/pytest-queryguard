"""CLEAN: the same list view, with ``selectinload(Customer.orders)``.

One query for the customers and one for all their orders, however many rows.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def customer_list_view(session: Session) -> list[dict[str, object]]:
    stmt = (
        select(shop.Customer)
        .options(selectinload(shop.Customer.orders))
        .order_by(shop.Customer.id)
    )
    return [
        {"name": c.name, "order_count": len(c.orders)}
        for c in session.scalars(stmt).all()
    ]


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        rows = customer_list_view(session)
    assert len(rows) == 8
