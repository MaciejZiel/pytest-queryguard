"""BUG repeated-statement: streaming orders with ``yield_per`` and a query per row.

The outer read is streamed in batches of five, which is fine; the count of
lines inside the loop runs once per order. Streaming the outer query does not
batch the inner one.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        report = []
        for order in session.scalars(select(shop.Order)).yield_per(5):
            lines = session.scalar(
                select(func.count())
                .select_from(shop.OrderLine)
                .where(shop.OrderLine.order_id == order.id)
            )
            report.append((order.id, lines))
    assert len(report) == 24
