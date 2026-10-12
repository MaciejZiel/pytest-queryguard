"""BUG column-load: three objects reload after ``commit()``.

Below the column-load threshold of 5, so the default configuration lets it
pass; with the threshold at 2 the plugin reports it. The verdict is BUG when
the three objects come from a loop whose length is data-dependent.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, COLUMN, Ctx

VERDICT = BUG
KINDS = frozenset({COLUMN})
KNOWN = {
    "*": "the column-load threshold is 5; queryguard_column_load_threshold = 2 catches it",
    "column-load-2": None,
    "all-opt-in": None,
}
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        orders = session.scalars(select(shop.Order).limit(3)).all()
        for order in orders:
            order.status = "paid"
        session.commit()
        statuses = [o.status for o in orders]
    assert statuses == ["paid"] * 3
