"""CLEAN: keyset pagination, ``WHERE id > :last ORDER BY id LIMIT 4``, until empty.

Only the keyset bound changes from page to page, and it only grows: the repeat
rule recognises the walk from the parameters.
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
        last = 0
        while True:
            page = session.scalars(
                select(shop.Order)
                .where(shop.Order.id > last)
                .order_by(shop.Order.id)
                .limit(4)
            ).all()
            if not page:
                break
            seen.extend(o.id for o in page)
            last = page[-1].id
    assert len(seen) == 24
