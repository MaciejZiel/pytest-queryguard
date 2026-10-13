"""BUG repeated-statement: ``SELECT ... FOR UPDATE`` once per row to reserve stock.

Each product is locked and read in its own statement. One
``select(...).where(id.in_(ids)).with_for_update()`` locks the same rows in one
round trip (SQLite ignores ``FOR UPDATE``; the statements still run per row).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session, session.begin():
        reserved = []
        for product_id in range(1, 9):
            product = session.scalars(
                select(shop.Product)
                .where(shop.Product.id == product_id)
                .with_for_update()
            ).one()
            reserved.append(product.id)
    assert len(reserved) == 8
