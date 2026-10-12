"""BUG repeated-statement and write-loop: ``session.merge()`` for each incoming row.

An import merges 10 existing products; each ``merge`` loads the row by primary
key first, a SELECT per row, and that SELECT autoflushes the previous row's
UPDATE, so the writes go out one row at a time too.
"""

from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REPEAT, WRITE, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT, WRITE})
setup = shop.setup


def run(ctx: Ctx) -> None:
    incoming = [{"id": i, "name": f"renamed{i}", "price": 5 * i} for i in range(1, 11)]
    with Session(ctx.engine()) as session:
        for row in incoming:
            session.merge(shop.Product(**row))
        session.commit()
