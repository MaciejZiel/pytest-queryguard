"""BUG repeated-statement: ``session.merge()`` for each incoming row.

An import merges 10 existing products; each ``merge`` loads the row by primary
key first, a SELECT per row (the UPDATEs are writes and not counted here).
"""

from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
setup = shop.setup


def run(ctx: Ctx) -> None:
    incoming = [{"id": i, "name": f"renamed{i}", "price": 5 * i} for i in range(1, 11)]
    with Session(ctx.engine()) as session:
        for row in incoming:
            session.merge(shop.Product(**row))
        session.commit()
