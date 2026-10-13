"""CLEAN: lock the rows of a reservation in one ``SELECT ... FOR UPDATE``.

The fix of ``bug_56_with_for_update_per_row``: one statement locks and reads
eight products, the changes go out in one flush.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session, session.begin():
        products = session.scalars(
            select(shop.Product)
            .where(shop.Product.id.in_(range(1, 9)))
            .with_for_update()
        ).all()
        for product in products:
            product.price -= 1
    assert len(products) == 8
