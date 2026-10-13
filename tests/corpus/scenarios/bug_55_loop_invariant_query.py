"""BUG repeated-statement: the same query with the same parameters on every line.

The tax rate of the shop's category does not depend on the order line, but the
loop asks for it once per line: twelve identical statements. Moving the query
out of the loop is the fix. Every run has the same parameters, so the
statements look like a polling loop; only the code tells them apart.
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
        lines = session.scalars(
            select(shop.OrderLine).where(shop.OrderLine.order_id <= 6)
        ).all()
        totals = []
        for line in lines:
            rate = session.scalar(
                select(shop.Category.id).where(shop.Category.name == "cat1")
            )
            totals.append(line.qty * (100 + rate))
    assert len(totals) == 12
