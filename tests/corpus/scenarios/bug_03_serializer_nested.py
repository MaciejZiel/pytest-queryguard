"""BUG relationship-load: a hand-written serializer walks two relationships.

``order_to_dict`` reads ``order.customer`` (many-to-one) and ``order.lines``
(one-to-many) of every order from a plain ``select(Order)``: a query per
customer and one per order.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REL, Ctx

VERDICT = BUG
KINDS = frozenset({REL})
setup = shop.setup


def order_to_dict(order: shop.Order) -> dict[str, object]:
    return {
        "id": order.id,
        "customer": order.customer.name,
        "lines": [{"product_id": line.product_id, "qty": line.qty} for line in order.lines],
    }


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        payload = [order_to_dict(o) for o in session.scalars(select(shop.Order))]
    assert len(payload) == 24
