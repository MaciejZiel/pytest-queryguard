"""BUG unused-eager-load: ``selectinload(Customer.orders)`` that nothing reads.

The endpoint only needs customer names; the eager load costs a second query.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..domains import shop
from ..kit import BUG, UNUSED, Ctx

VERDICT = BUG
KINDS = frozenset({UNUSED})
setup = shop.setup


def customer_names(session: Session) -> list[str]:
    stmt = select(shop.Customer).options(selectinload(shop.Customer.orders))
    return [c.name for c in session.scalars(stmt)]


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        names = customer_names(session)
    assert len(names) == 8
