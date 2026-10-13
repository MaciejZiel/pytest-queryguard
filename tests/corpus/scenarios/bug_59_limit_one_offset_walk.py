"""BUG repeated-statement: reading a table one row at a time with ``LIMIT 1 OFFSET n``.

It is pagination with a page size of one: twenty-four rows, twenty-five
statements. A page of a few hundred rows, or one query, reads the same rows.
This is the case issue #60 worried about if pagination were skipped by default.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
setup = shop.setup


def run(ctx: Ctx) -> None:
    totals = []
    with Session(ctx.engine()) as session:
        index = 0
        while True:
            order = session.scalars(
                select(shop.Order).order_by(shop.Order.id).limit(1).offset(index)
            ).first()
            if order is None:
                break
            totals.append(order.total)
            index += 1
    assert len(totals) == 24
