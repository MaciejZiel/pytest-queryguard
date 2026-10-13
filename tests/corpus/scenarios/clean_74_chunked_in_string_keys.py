"""CLEAN: looking up 1100 products by name in ``IN`` chunks of 200.

Six statements (the last chunk has 100 names) whose lists partition one list of
string keys: batching by a natural key instead of an integer id.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
CHUNK = 200


def setup(ctx: Ctx) -> None:
    shop.setup(ctx, customers=1, orders=1, lines=1, products=1100, tags=1)


def run(ctx: Ctx) -> None:
    names = [f"product{i}" for i in range(1, 1101)]
    prices: dict[str, int] = {}
    with Session(ctx.engine()) as session:
        for start in range(0, len(names), CHUNK):
            chunk = names[start : start + CHUNK]
            rows = session.execute(
                select(shop.Product.name, shop.Product.price).where(
                    shop.Product.name.in_(chunk)
                )
            )
            prices.update(rows.tuples().all())
    assert len(prices) == 1100
