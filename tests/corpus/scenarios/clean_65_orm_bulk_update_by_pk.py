"""CLEAN: ``session.execute(update(Product), rows)``, an ORM bulk UPDATE by primary key.

Forty new prices in one call: the ORM sends them as one ``executemany``.
"""

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()


def setup(ctx: Ctx) -> None:
    shop.setup(ctx, products=40)


def run(ctx: Ctx) -> None:
    rows = [{"id": i, "price": 3 * i} for i in range(1, 41)]
    with Session(ctx.engine()) as session:
        session.execute(update(shop.Product), rows)
        session.commit()
        assert session.get(shop.Product, 40).price == 120
