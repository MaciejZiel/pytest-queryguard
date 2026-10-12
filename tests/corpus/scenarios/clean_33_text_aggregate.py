"""CLEAN: one ``text()`` aggregate with a GROUP BY."""

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        rows = session.execute(
            text('SELECT customer_id, SUM(total) FROM "order" GROUP BY customer_id')
        ).all()
    assert len(rows) == 8
