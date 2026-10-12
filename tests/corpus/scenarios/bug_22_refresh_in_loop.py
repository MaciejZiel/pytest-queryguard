"""BUG column-load: ``session.refresh()`` for every object.

Refreshing each order to pick up a trigger-computed column is a SELECT by
primary key per object, which the plugin reports as a refresh column load.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, COLUMN, Ctx

VERDICT = BUG
KINDS = frozenset({COLUMN})
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        orders = session.scalars(select(shop.Order)).all()
        for order in orders:
            session.refresh(order)
    assert len(orders) == 24
