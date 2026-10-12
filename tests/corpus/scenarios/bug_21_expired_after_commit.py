"""BUG column-load: attributes read after ``commit()`` with the default expiry.

A service marks every order shipped, commits, then builds a response from the
same objects. ``expire_on_commit`` is on, so each object reloads its row on the
first attribute access.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, COLUMN, Ctx

VERDICT = BUG
KINDS = frozenset({COLUMN})
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        orders = session.scalars(select(shop.Order)).all()
        for order in orders:
            order.status = "shipped"
        session.commit()
        response = [{"id": o.id, "status": o.status} for o in orders]
    assert len(response) == 24
