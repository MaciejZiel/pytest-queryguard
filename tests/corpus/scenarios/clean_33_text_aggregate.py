"""CLEAN: one ``text()`` aggregate with a GROUP BY."""

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    # "order" on SQLite and PostgreSQL, `order` on MySQL
    table = ctx.engine().dialect.identifier_preparer.quote("order")
    with Session(ctx.engine()) as session:
        rows = session.execute(
            text(f"SELECT customer_id, SUM(total) FROM {table} GROUP BY customer_id")
        ).all()
    assert len(rows) == 8
