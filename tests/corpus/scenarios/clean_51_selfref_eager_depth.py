"""CLEAN: the tree walk with ``selectinload(...).selectinload(...)`` to depth two."""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..domains import shop
from ..kit import CLEAN, Ctx
from .bug_37_recursive_tree_walk import setup as tree_setup

VERDICT = CLEAN
KINDS = frozenset()
setup = tree_setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        stmt = (
            select(shop.Category)
            .where(shop.Category.parent_id.is_(None))
            .options(
                selectinload(shop.Category.children).selectinload(shop.Category.children)
            )
        )
        root = session.scalars(stmt).one()
        # The leaves still have a lazy ``children``: the walk is cut at depth two.
        lines = [root.name] + [c.name for child in root.children for c in child.children]
    assert len(lines) == 10
