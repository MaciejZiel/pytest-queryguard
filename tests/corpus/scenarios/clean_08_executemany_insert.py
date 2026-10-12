"""CLEAN: ``session.execute(insert(Tag), [rows])``, an executemany of 50 rows."""

from sqlalchemy import insert
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        session.execute(insert(shop.Tag), [{"name": f"bulk{i}"} for i in range(50)])
        session.commit()
