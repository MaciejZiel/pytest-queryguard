"""BUG repeated-statement: the next three orders, queried once per order.

For every order (in id order) a "next orders" widget runs
``WHERE id > :id ORDER BY id LIMIT 3``. Only the range bound changes and it
only grows, like keyset pagination, but the bound is each row's own id, not the
last id of the previous page: twelve statements for twelve rows.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        orders = session.scalars(
            select(shop.Order).order_by(shop.Order.id).limit(12)
        ).all()
        following = {
            order.id: session.scalars(
                select(shop.Order.id)
                .where(shop.Order.id > order.id)
                .order_by(shop.Order.id)
                .limit(3)
            ).all()
            for order in orders
        }
    assert len(following) == 12
