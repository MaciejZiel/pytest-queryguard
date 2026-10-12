"""BUG repeated-statement: ``with_parent`` once per customer.

``with_parent(customer, Customer.orders)`` builds the lazy-load query by hand.
It is not the lazy loader, so it shows up as the same SELECT run per customer.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, with_parent

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        totals = {}
        for customer in session.scalars(select(shop.Customer)):
            stmt = select(shop.Order).where(with_parent(customer, shop.Customer.orders))
            totals[customer.id] = sum(o.total for o in session.scalars(stmt))
    assert len(totals) == 8
