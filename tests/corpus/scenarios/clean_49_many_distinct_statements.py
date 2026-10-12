"""CLEAN: eight different statements, none of them repeated."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        counts = [
            session.scalar(select(func.count()).select_from(model))
            for model in (
                shop.Customer,
                shop.Order,
                shop.OrderLine,
                shop.Product,
                shop.Tag,
                shop.Category,
            )
        ]
        top = session.scalar(select(func.max(shop.Order.total)))
        low = session.scalar(select(func.min(shop.Product.price)))
    assert counts[0] == 8 and top and low
