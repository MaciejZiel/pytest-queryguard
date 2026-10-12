"""BUG write-loop: ``add()`` and ``flush()`` for every new tag.

The flush is there to read the new primary key; ``add_all()`` followed by one
``flush()`` assigns all of them at once.
"""

from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, WRITE, Ctx

VERDICT = BUG
KINDS = frozenset({WRITE})
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        ids = []
        for i in range(10):
            tag = shop.Tag(name=f"extra{i}")
            session.add(tag)
            session.flush()
            ids.append(tag.id)
        session.commit()
    assert len(set(ids)) == 10
