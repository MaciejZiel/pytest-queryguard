"""CLEAN: three savepoints around three different statements."""

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        with session.begin_nested():
            session.execute(
                update(shop.Order).where(shop.Order.id == 1).values(status="paid")
            )
        with session.begin_nested():
            total = session.scalar(select(func.sum(shop.Order.total)))
        with session.begin_nested():
            names = list(session.scalars(select(shop.Customer.name)))
        session.commit()
    assert total and len(names) == 8
