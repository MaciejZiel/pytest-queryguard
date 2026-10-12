"""CLEAN: the whole category tree with one recursive CTE."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx
from .bug_37_recursive_tree_walk import setup as tree_setup

VERDICT = CLEAN
KINDS = frozenset()
setup = tree_setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        tree = (
            select(shop.Category.id, shop.Category.name)
            .where(shop.Category.parent_id.is_(None))
            .cte(recursive=True)
        )
        child = select(shop.Category.id, shop.Category.name).join(
            tree, shop.Category.parent_id == tree.c.id
        )
        rows = session.execute(select(tree.union_all(child))).all()
    assert len(rows) == 13
