"""BUG repeated-statement: a moving window read with ``LIMIT 3 OFFSET i``, once per row.

A moving average over order totals reads three rows at offset ``i`` for every
``i``: the offset grows by one while the page holds three rows, so the pages
overlap. One statement over every total and the window computed in Python is
the fix.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
setup = shop.setup


def run(ctx: Ctx) -> None:
    averages: list[float] = []
    with Session(ctx.engine()) as session:
        for i in range(10):
            window = session.scalars(
                select(shop.Order.total).order_by(shop.Order.id).limit(3).offset(i)
            ).all()
            averages.append(sum(window) / len(window))
    assert len(averages) == 10
