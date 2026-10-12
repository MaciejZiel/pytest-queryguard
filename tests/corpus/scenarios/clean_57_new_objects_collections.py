"""CLEAN: new objects' collections are read without any query.

Ten orders are built in memory; reading ``order.lines`` of a pending object is
an empty list, not a lazy load.
"""

from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    orders = [shop.Order(customer_id=1) for _ in range(10)]
    with Session(ctx.engine()) as session:
        session.add_all(orders)
        empty = [len(o.lines) for o in orders]
        session.commit()
    assert empty == [0] * 10
