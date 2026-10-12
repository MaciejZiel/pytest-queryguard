"""CLEAN: one ``insert().values([...])`` with 50 rows in a multi-row VALUES list."""

from sqlalchemy import insert
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        session.execute(insert(shop.Tag).values([{"name": f"multi{i}"} for i in range(50)]))
        session.commit()
