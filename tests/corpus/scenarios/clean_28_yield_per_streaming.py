"""CLEAN: ``yield_per(5)`` streams 24 rows in batches from one cursor."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        ids = [o.id for o in session.scalars(select(shop.Order)).yield_per(5)]
    assert len(ids) == 24
