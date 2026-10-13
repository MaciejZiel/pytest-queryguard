"""CLEAN: ``session.execute(insert(OrderLine), rows)`` with 200 dicts.

The ORM bulk INSERT of a list goes out as one ``executemany`` (or a few
``insertmanyvalues`` batches), not a statement per row.
"""

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    rows = [
        {"order_id": 1 + i % 24, "product_id": 1 + i % 12, "qty": 1} for i in range(200)
    ]
    with Session(ctx.engine()) as session:
        session.execute(insert(shop.OrderLine), rows)
        session.commit()
        total = session.scalar(select(func.count()).select_from(shop.OrderLine))
    assert total == 48 + 200
