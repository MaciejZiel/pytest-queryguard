"""CLEAN: ``selectinload(Customer.orders).selectinload(Order.lines)``, all levels loaded."""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        stmt = select(shop.Customer).options(
            selectinload(shop.Customer.orders).selectinload(shop.Order.lines)
        )
        units = sum(
            line.qty
            for customer in session.scalars(stmt)
            for order in customer.orders
            for line in order.lines
        )
    assert units == 72
