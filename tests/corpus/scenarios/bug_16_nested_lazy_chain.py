"""BUG relationship-load: orders, their lines and each line's product, all lazy.

Three levels of loops over default relationships: ``order.lines`` per order and
``line.product`` per product that was not loaded yet.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REL, Ctx

VERDICT = BUG
KINDS = frozenset({REL})
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        names = [
            line.product.name
            for order in session.scalars(select(shop.Order))
            for line in order.lines
        ]
    assert len(names) == 48
