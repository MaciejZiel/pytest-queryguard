"""CLEAN: one Core join returns customer names with their order totals."""

from sqlalchemy import select

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    customer, order = shop.Customer.__table__, shop.Order.__table__
    stmt = select(customer.c.name, order.c.total).join(
        order, order.c.customer_id == customer.c.id
    )
    with ctx.engine().connect() as conn:
        rows = conn.execute(stmt).all()
    assert len(rows) == 24
