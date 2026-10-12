"""CLEAN: loading 1500 products in ``IN`` chunks of 500, three statements.

Batching is the fix for N+1, and three statements are below the repeat threshold."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
KNOWN = {
    "small-repeat": "three statements of one shape from one line are what the small-repeat rule reports"
}
CHUNK = 500


def setup(ctx: Ctx) -> None:
    shop.setup(ctx, customers=1, orders=1, lines=1, products=1500)


def run(ctx: Ctx) -> None:
    ids = list(range(1, 1501))
    names: list[str] = []
    with Session(ctx.engine()) as session:
        for start in range(0, len(ids), CHUNK):
            chunk = ids[start : start + CHUNK]
            names.extend(
                session.scalars(
                    select(shop.Product.name).where(shop.Product.id.in_(chunk))
                )
            )
    assert len(names) == 1500
