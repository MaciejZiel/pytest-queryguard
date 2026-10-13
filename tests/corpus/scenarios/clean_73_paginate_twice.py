"""CLEAN: an export that pages through the orders, run twice by the test.

The test checks that two exports agree, so the same pagination runs twice: the
offset goes 0, 8, 16, 24 and starts again at 0. Each walk is pagination.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def export(session: Session) -> list[int]:
    out: list[int] = []
    offset = 0
    while True:
        page = session.scalars(
            select(shop.Order.total).order_by(shop.Order.id).limit(8).offset(offset)
        ).all()
        if not page:
            return out
        out.extend(page)
        offset += 8


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        first = export(session)
        second = export(session)
    assert first == second and len(first) == 24
