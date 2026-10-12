"""CLEAN: a SQLite ``INSERT ... ON CONFLICT DO NOTHING`` executemany of 20 rows."""

from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    rows = [{"id": i, "name": f"upsert{i}"} for i in range(1, 21)]
    with Session(ctx.engine()) as session:
        session.execute(insert(shop.Tag).on_conflict_do_nothing(), rows)
        session.commit()
