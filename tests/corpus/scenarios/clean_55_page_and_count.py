"""CLEAN: a list page runs one page query and one ``COUNT``."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        total = session.scalar(select(func.count()).select_from(shop.Order))
        page = session.scalars(
            select(shop.Order).order_by(shop.Order.id).limit(10)
        ).all()
    assert total == 24 and len(page) == 10
