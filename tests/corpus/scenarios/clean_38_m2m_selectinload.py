"""CLEAN: ``selectinload(Product.tags)`` over the association table."""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        stmt = select(shop.Product).options(selectinload(shop.Product.tags))
        labels = {p.name: sorted(t.name for t in p.tags) for p in session.scalars(stmt)}
    assert len(labels) == 12
