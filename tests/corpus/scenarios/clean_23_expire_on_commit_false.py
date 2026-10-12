"""CLEAN: ``sessionmaker(expire_on_commit=False)``, attributes read after commit."""

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    factory = sessionmaker(ctx.engine(), expire_on_commit=False)
    with factory() as session:
        orders = session.scalars(select(shop.Order)).all()
        for order in orders:
            order.status = "shipped"
        session.commit()
        response = [{"id": o.id, "status": o.status} for o in orders]
    assert len(response) == 24
