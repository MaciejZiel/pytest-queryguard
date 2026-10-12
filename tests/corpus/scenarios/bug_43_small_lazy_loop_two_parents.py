"""BUG relationship-load: the smallest lazy-load loop, two parents.

Two customers, two lazy loads. The relationship threshold is 2, so this is
reported by default.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REL, Ctx

VERDICT = BUG
KINDS = frozenset({REL})


def setup(ctx: Ctx) -> None:
    shop.setup(ctx, customers=2)


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        totals = [
            sum(o.total for o in c.orders)
            for c in session.scalars(select(shop.Customer))
        ]
    assert len(totals) == 2
