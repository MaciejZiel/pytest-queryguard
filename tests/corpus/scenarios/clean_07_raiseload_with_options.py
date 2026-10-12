"""CLEAN: ``raiseload("*")`` plus the one relationship the code reads.

Anything the view forgot to load would raise instead of querying; the view reads
only ``orders``, which is loaded.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, raiseload, selectinload

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        stmt = select(shop.Customer).options(
            selectinload(shop.Customer.orders), raiseload("*")
        )
        counts = [len(c.orders) for c in session.scalars(stmt)]
    assert counts == [3] * 8
