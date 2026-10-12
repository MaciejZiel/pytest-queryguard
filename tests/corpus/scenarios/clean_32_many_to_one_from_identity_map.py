"""CLEAN: ``order.customer`` is answered from the identity map.

The customers are loaded first and kept referenced (the identity map holds
objects weakly); the lazy many-to-one for each order finds its
customer in the session and runs no SQL.
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
        customers = session.scalars(select(shop.Customer)).all()
        names = [o.customer.name for o in session.scalars(select(shop.Order))]
    assert len(names) == 24 and len(customers) == 8
