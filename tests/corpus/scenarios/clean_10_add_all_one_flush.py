"""CLEAN: 50 objects, one ``add_all()`` and one commit.

The unit of work may send one INSERT per row on some dialects; that is one flush,
and it is how the ORM batches.
"""

from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        session.add_all(shop.Tag(name=f"orm{i}") for i in range(50))
        session.commit()
