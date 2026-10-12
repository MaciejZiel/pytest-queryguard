"""BUG write-loop: one ``UPDATE ... WHERE id = ?`` per order.

Marking orders shipped with a statement per row; ``update().where(Order.id.in_())``
would be one.
"""

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, WRITE, Ctx

VERDICT = BUG
KINDS = frozenset({WRITE})
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        ids = list(session.scalars(select(shop.Order.id)))
        for order_id in ids:
            session.execute(
                update(shop.Order).where(shop.Order.id == order_id).values(status="shipped")
            )
        session.commit()
    assert len(ids) == 24
