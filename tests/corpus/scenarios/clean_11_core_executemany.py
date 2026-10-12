"""CLEAN: a Core ``conn.execute(insert(table), rows)`` with a list of 50 dicts."""

from sqlalchemy import insert

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    with ctx.engine().begin() as conn:
        conn.execute(
            insert(shop.Tag.__table__), [{"name": f"core{i}"} for i in range(50)]
        )
