"""CLEAN: walking all orders with ``LIMIT`` and ``OFFSET`` pages of four.

Seven statements (six pages and a final empty one) are the same statement with
other offsets, which is what paging is. The repeat rule recognises the walk from
the parameters: only the offset changes, by at least the page size.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    seen = []
    with Session(ctx.engine()) as session:
        offset = 0
        while True:
            page = session.scalars(
                select(shop.Order).order_by(shop.Order.id).limit(4).offset(offset)
            ).all()
            if not page:
                break
            seen.extend(o.id for o in page)
            offset += 4
    assert len(seen) == 24
