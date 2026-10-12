"""BUG repeated-statement: a ``COUNT(*)`` per customer for a dashboard column."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        rows = []
        for customer in session.scalars(select(shop.Customer)):
            count = session.scalar(
                select(func.count())
                .select_from(shop.Order)
                .where(shop.Order.customer_id == customer.id)
            )
            rows.append((customer.name, count))
    assert len(rows) == 8
