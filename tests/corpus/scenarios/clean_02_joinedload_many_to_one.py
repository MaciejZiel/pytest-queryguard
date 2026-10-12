"""CLEAN: ``joinedload(Order.customer)`` for a many-to-one read in a loop.

One statement joins the customer to each order, however many orders there are.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        stmt = select(shop.Order).options(joinedload(shop.Order.customer))
        names = [o.customer.name for o in session.scalars(stmt)]
    assert len(names) == 24
