"""CLEAN: loading 3000 products in ``IN`` chunks of 500, six statements.

The batching is the fix for N+1, but the six statements have one shape (the
``IN`` list is collapsed), so the repeat rule at its default threshold of 5
reports them.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
KNOWN = {
    "*": "chunked IN statements share one shape; raise queryguard_repeat_threshold or mark the test allow_nplusone",
}
CHUNK = 500


def setup(ctx: Ctx) -> None:
    shop.setup(ctx, customers=1, orders=1, lines=1, products=3000, tags=1)


def run(ctx: Ctx) -> None:
    ids = list(range(1, 3001))
    names: list[str] = []
    with Session(ctx.engine()) as session:
        for start in range(0, len(ids), CHUNK):
            chunk = ids[start : start + CHUNK]
            names.extend(
                session.scalars(
                    select(shop.Product.name).where(shop.Product.id.in_(chunk))
                )
            )
    assert len(names) == 3000
