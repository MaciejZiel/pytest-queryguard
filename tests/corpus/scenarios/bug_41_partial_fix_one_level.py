"""BUG relationship-load: ``selectinload`` fixed one level, the next one is lazy.

The customers' orders are loaded eagerly, but ``order.lines`` is read inside the
loop without being in the option chain.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..domains import shop
from ..kit import BUG, REL, Ctx

VERDICT = BUG
KINDS = frozenset({REL})
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        stmt = select(shop.Customer).options(selectinload(shop.Customer.orders))
        units = sum(
            line.qty
            for customer in session.scalars(stmt)
            for order in customer.orders
            for line in order.lines
        )
    assert units == 72
