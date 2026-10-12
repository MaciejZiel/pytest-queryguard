"""BUG relationship-load: a recursive walk over ``Category.children``.

Each node's children are lazy-loaded when the recursion reaches it: one SELECT
per node of the tree.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REL, Ctx

VERDICT = BUG
KINDS = frozenset({REL})


def setup(ctx: Ctx) -> None:
    ctx.reset()
    shop.Base.metadata.create_all(ctx.engine())
    with Session(ctx.engine()) as session:
        root = shop.Category(id=1, name="root")
        next_id = 2
        for _ in range(3):
            child = shop.Category(id=next_id, name=f"c{next_id}", parent=root)
            next_id += 1
            for _ in range(3):
                shop.Category(id=next_id, name=f"c{next_id}", parent=child)
                next_id += 1
        session.add(root)
        session.commit()


def walk(category: shop.Category, depth: int = 0) -> list[str]:
    lines = ["  " * depth + category.name]
    for child in category.children:
        lines.extend(walk(child, depth + 1))
    return lines


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        root = session.scalars(
            select(shop.Category).where(shop.Category.parent_id.is_(None))
        ).one()
        lines = walk(root)
    assert len(lines) == 13
