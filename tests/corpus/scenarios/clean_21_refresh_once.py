"""CLEAN: one ``session.refresh()`` of a single object after a bulk UPDATE."""

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        order = session.get(shop.Order, 1)
        session.execute(
            update(shop.Order).where(shop.Order.id == 1).values(status="paid")
        )
        session.refresh(order)
    assert order.status == "paid"
