"""CLEAN: ``subqueryload(Customer.orders)``.

Two statements in total: the customers and one subquery for all their orders.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, subqueryload

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        stmt = select(shop.Customer).options(subqueryload(shop.Customer.orders))
        counts = [len(c.orders) for c in session.scalars(stmt)]
    assert counts == [3] * 8
