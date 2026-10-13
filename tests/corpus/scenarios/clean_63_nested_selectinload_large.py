"""CLEAN: ``selectinload`` two levels deep over 1500 customers.

SQLAlchemy loads ``Customer.orders`` and then ``Order.lines`` in ``IN`` chunks of
500 parents each: several statements of one shape per level, all of them the
ORM's own batching of one eager load.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()


def setup(ctx: Ctx) -> None:
    shop.setup(ctx, customers=1500, orders=1, lines=1, products=2, tags=1)


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        stmt = select(shop.Customer).options(
            selectinload(shop.Customer.orders).selectinload(shop.Order.lines)
        )
        lines = sum(len(o.lines) for c in session.scalars(stmt) for o in c.orders)
    assert lines == 1500
