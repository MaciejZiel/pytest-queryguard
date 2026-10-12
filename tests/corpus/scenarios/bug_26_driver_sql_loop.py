"""BUG repeated-statement: ``exec_driver_sql`` with a positional parameter, per id."""

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
setup = shop.setup


def run(ctx: Ctx) -> None:
    # Positional "?" is SQLite's paramstyle, "%s" is pg8000's.
    marker = "%s" if ctx.pg_url else "?"
    sql = f"SELECT name FROM product WHERE id = {marker}"
    with ctx.engine().connect() as conn:
        names = [conn.exec_driver_sql(sql, (i,)).scalar_one() for i in range(1, 11)]
    assert len(names) == 10
