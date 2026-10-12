"""BUG repeated-statement: a new ``Session`` for every id.

A helper opens its own session, as service functions often do; a loop over ids
calls it, so the lookup runs once per id on a fresh session and connection checkout.
"""

from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
setup = shop.setup


def product_name(ctx: Ctx, product_id: int) -> str:
    with Session(ctx.engine()) as session:
        return session.get(shop.Product, product_id).name


def run(ctx: Ctx) -> None:
    names = [product_name(ctx, i) for i in range(1, 11)]
    assert len(names) == 10
