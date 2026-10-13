"""BUG repeated-statement: the latest three orders, one query per customer.

Each query has ``LIMIT`` (and on SQLite ``OFFSET``), like a page, but the pages
belong to different customers: it is a query per item of the customer list,
not a walk through one result. A window function or ``selectinload`` with a
filter does it in one statement.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        latest = {}
        for customer in session.scalars(select(shop.Customer)).all():
            latest[customer.id] = session.scalars(
                select(shop.Order.id)
                .where(shop.Order.customer_id == customer.id)
                .order_by(shop.Order.id.desc())
                .limit(3)
            ).all()
    assert len(latest) == 8 and all(len(ids) == 3 for ids in latest.values())
