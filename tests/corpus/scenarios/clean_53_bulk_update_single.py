"""CLEAN: one ``UPDATE ... WHERE id IN (...)`` for 12 orders."""

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        ids = list(session.scalars(select(shop.Order.id).limit(12)))
        session.execute(
            update(shop.Order).where(shop.Order.id.in_(ids)).values(status="shipped")
        )
        session.commit()
    assert len(ids) == 12
