"""BUG repeated-statement: the legacy ``session.query()`` API in a loop.

Old code bases still spell it ``session.query(Order).filter_by(...).all()``.
"""

from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        totals = {}
        for customer in session.query(shop.Customer).all():
            orders = session.query(shop.Order).filter_by(customer_id=customer.id).all()
            totals[customer.id] = sum(o.total for o in orders)
    assert len(totals) == 8
