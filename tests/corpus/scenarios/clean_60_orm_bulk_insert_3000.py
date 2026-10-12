"""CLEAN: ``add_all`` of 3000 objects: one flush, sent in several INSERT batches."""

from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        session.add_all(shop.Tag(name=f"bulk{i}") for i in range(3000))
        session.commit()
