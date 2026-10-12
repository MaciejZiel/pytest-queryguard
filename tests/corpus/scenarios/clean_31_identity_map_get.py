"""CLEAN: ``session.get`` for ids that are already in the identity map.

One query loads all products and the list keeps them alive; the 12 ``get`` calls after it are answered from
the session without SQL.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        loaded = session.scalars(select(shop.Product)).all()
        found = [session.get(shop.Product, i) for i in range(1, 13)]
    assert all(found) and len(loaded) == 12
