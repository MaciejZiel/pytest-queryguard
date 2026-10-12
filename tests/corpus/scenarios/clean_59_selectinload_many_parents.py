"""CLEAN: ``selectinload`` over 1200 parents, loaded in chunks of 500 by the ORM.

The extra statements of the eager load are the ORM's own batching; they must not
look like a loop.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()


def setup(ctx: Ctx) -> None:
    shop.setup(ctx, customers=3000, orders=1, lines=1, products=2, tags=1)


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        stmt = select(shop.Customer).options(selectinload(shop.Customer.orders))
        counts = [len(c.orders) for c in session.scalars(stmt)]
    assert len(counts) == 3000
