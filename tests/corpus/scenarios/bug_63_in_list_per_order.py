"""BUG repeated-statement: an ``IN`` list built from each order's own lines.

For every order the products of its two lines are loaded with
``Product.id.in_(...)``. Each statement has an ``IN`` list, but the lists come
from the items of a loop, not from slicing one list; ``selectinload`` of
``Order.lines`` and ``OrderLine.product`` loads them in two statements.
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
        rows = session.execute(
            select(shop.OrderLine.order_id, shop.OrderLine.product_id)
        ).all()
        by_order: dict[int, list[int]] = {}
        for order_id, product_id in rows:
            by_order.setdefault(order_id, []).append(product_id)
        names = {}
        for order_id, product_ids in by_order.items():
            names[order_id] = session.scalars(
                select(shop.Product.name).where(shop.Product.id.in_(product_ids))
            ).all()
    assert len(names) == 24
