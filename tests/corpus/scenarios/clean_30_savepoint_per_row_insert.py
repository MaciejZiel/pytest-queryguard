"""CLEAN: a savepoint and an INSERT per row, so one duplicate does not abort the rest.

An idempotent import: each row is inserted inside ``begin_nested()`` and an
``IntegrityError`` skips only that row. It is a loop of single-row INSERTs on
purpose; the opt-in write-loop rule reports it.
"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
KNOWN = {
    "write-loop": "per-row inserts with error handling per row are a legitimate loop"
}
setup = shop.setup


def run(ctx: Ctx) -> None:
    created = 0
    with Session(ctx.engine()) as session:
        for tag_id in [1, 2, 100, 101, 102, 103, 104, 105]:
            try:
                with session.begin_nested():
                    session.add(shop.Tag(id=tag_id, name=f"import{tag_id}"))
                created += 1
            except IntegrityError:
                continue
        session.commit()
    assert created == 6
