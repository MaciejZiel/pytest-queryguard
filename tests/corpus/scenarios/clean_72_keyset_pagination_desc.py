"""CLEAN: newest-first keyset pagination, ``WHERE id < :last ORDER BY id DESC LIMIT 5``.

Twenty-four orders in pages of five: five pages and a final empty one. The
cursor moves down, the page size and the filter stay the same.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    seen: list[int] = []
    with Session(ctx.engine()) as session:
        last = 10**9
        while True:
            page = session.scalars(
                select(shop.Order.id)
                .where(shop.Order.id < last, shop.Order.status == "new")
                .order_by(shop.Order.id.desc())
                .limit(5)
            ).all()
            if not page:
                break
            seen.extend(page)
            last = page[-1]
    assert seen == list(range(24, 0, -1))
