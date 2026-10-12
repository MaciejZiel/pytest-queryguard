"""CLEAN: ``order.customer = customer`` with ``back_populates`` loads nothing.

Attaching ten new orders to persistent customers fires the backref handler,
which appends without loading the customers' ``orders`` collections.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        customers = session.scalars(select(shop.Customer)).all()
        for i in range(10):
            session.add(shop.Order(customer=customers[i % 8]))
        session.commit()
