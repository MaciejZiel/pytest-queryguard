"""CLEAN: 200 rows are seeded one flush at a time, but in a fixture.

``setup()`` plays the part of a pytest fixture: it runs before the test body, so
its row-by-row inserts are not measured. The measured code is one query.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()


def setup(ctx: Ctx) -> None:
    ctx.reset()
    shop.Base.metadata.create_all(ctx.engine())
    with Session(ctx.engine()) as session:
        for i in range(200):
            session.add(shop.Tag(name=f"seed{i}"))
            session.flush()
        session.commit()


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        total = session.scalar(select(func.count()).select_from(shop.Tag))
    assert total == 200
