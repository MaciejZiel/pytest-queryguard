"""CLEAN: ``selectinload(Customer.orders)`` and the orders are read."""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        stmt = select(shop.Customer).options(selectinload(shop.Customer.orders))
        rows = [(c.name, sum(o.total for o in c.orders)) for c in session.scalars(stmt)]
    assert len(rows) == 8
