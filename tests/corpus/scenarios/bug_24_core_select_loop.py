"""BUG repeated-statement: a Core ``select()`` per id on one connection."""

from sqlalchemy import select

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
setup = shop.setup


def run(ctx: Ctx) -> None:
    product = shop.Product.__table__
    with ctx.engine().connect() as conn:
        names = [
            conn.execute(select(product.c.name).where(product.c.id == i)).scalar_one()
            for i in range(1, 11)
        ]
    assert len(names) == 10
