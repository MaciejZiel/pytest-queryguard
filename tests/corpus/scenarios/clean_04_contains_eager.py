"""CLEAN: an explicit join with ``contains_eager``.

The query already joins the customer for its filter; ``contains_eager`` tells
the ORM to fill ``Order.customer`` from those columns.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, contains_eager

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        stmt = (
            select(shop.Order)
            .join(shop.Order.customer)
            .where(shop.Customer.name.like("customer%"))
            .options(contains_eager(shop.Order.customer))
        )
        names = [o.customer.name for o in session.scalars(stmt)]
    assert len(names) == 24
