"""CLEAN: walking all orders with ``LIMIT`` and ``OFFSET`` pages of four.

Six pages are the same statement with other offsets, which is what paging is.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
KNOWN = {
    "*": "five or more pages are the same statement five times; only the small-repeat rule leaves pagination alone (issue #60)"
}
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
