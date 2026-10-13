"""CLEAN: one ``INSERT ... VALUES (...), (...) ON CONFLICT DO UPDATE`` for 30 rows.

The fix of ``bug_62_upsert_per_row``: a price feed upserted as one multi-row
statement.
"""

from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx, insert_for

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    feed = [{"id": i, "name": f"product{i}", "price": 7 * i} for i in range(1, 31)]
    stmt = insert_for(ctx.engine())(shop.Product).values(feed)
    stmt = stmt.on_conflict_do_update(
        index_elements=[shop.Product.id], set_={"price": stmt.excluded.price}
    )
    with Session(ctx.engine()) as session:
        session.execute(stmt)
        session.commit()
        assert session.get(shop.Product, 30).price == 210
