"""BUG write-loop: one ``DELETE`` per order line."""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, WRITE, Ctx

VERDICT = BUG
KINDS = frozenset({WRITE})
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        ids = list(session.scalars(select(shop.OrderLine.id).limit(12)))
        for line_id in ids:
            session.execute(delete(shop.OrderLine).where(shop.OrderLine.id == line_id))
        session.commit()
    assert len(ids) == 12
