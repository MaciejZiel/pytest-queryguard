"""BUG write-loop: an ``INSERT ... ON CONFLICT DO UPDATE`` per row.

The price feed is upserted one row per statement from one line. The same
upsert takes the list of rows as one ``executemany`` (see
``clean_54_upsert_executemany``) or one multi-row ``VALUES``.
"""

from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, WRITE, Ctx, insert_for

VERDICT = BUG
KINDS = frozenset({WRITE})
setup = shop.setup


def run(ctx: Ctx) -> None:
    feed = [{"id": i, "name": f"product{i}", "price": 7 * i} for i in range(1, 11)]
    insert = insert_for(ctx.engine())
    with Session(ctx.engine()) as session:
        for row in feed:
            stmt = insert(shop.Product).values(**row)
            session.execute(
                stmt.on_conflict_do_update(
                    index_elements=[shop.Product.id],
                    set_={"price": stmt.excluded.price},
                )
            )
        session.commit()
        assert session.get(shop.Product, 3).price == 21
