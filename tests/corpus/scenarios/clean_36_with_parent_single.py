"""CLEAN: one ``with_parent`` query for one customer."""

from sqlalchemy import select
from sqlalchemy.orm import Session, with_parent

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        customer = session.get(shop.Customer, 1)
        orders = session.scalars(select(shop.Order).where(with_parent(customer, shop.Customer.orders))).all()
    assert len(orders) == 3
