"""BUG repeated-statement: an ``IN`` list of each customer's ten orders, per customer.

A helper takes a list of order ids; it is called once per customer with that
customer's own orders. Every customer has ten orders, so every list has ten
values and no two lists share one: the statements look like chunks of one list,
but they come from a loop over customers. One statement over every order (or
``selectinload(Customer.orders)``) is the fix.
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})


def setup(ctx: Ctx) -> None:
    shop.setup(ctx, customers=8, orders=10, lines=1, products=4, tags=1)


def totals_for(session: Session, order_ids: Sequence[int]) -> int:
    return sum(
        session.scalars(select(shop.Order.total).where(shop.Order.id.in_(order_ids)))
    )


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        rows = session.execute(select(shop.Order.customer_id, shop.Order.id)).all()
        by_customer: dict[int, list[int]] = {}
        for customer_id, order_id in rows:
            by_customer.setdefault(customer_id, []).append(order_id)
        totals = {c: totals_for(session, ids) for c, ids in by_customer.items()}
    assert len(totals) == 8
