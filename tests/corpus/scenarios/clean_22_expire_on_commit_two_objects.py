"""CLEAN: two objects reload after ``commit()``, the default ``expire_on_commit``.

Reading two attributes after a commit is ordinary test code. The column-load
threshold is 5; at 2 this is reported.
"""

from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
KNOWN = {"column-load-2": "two reloads after a commit are the threshold of 2"}
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        first, second = session.get(shop.Order, 1), session.get(shop.Order, 2)
        first.status = second.status = "paid"
        session.commit()
        statuses = [first.status, second.status]
    assert statuses == ["paid", "paid"]
