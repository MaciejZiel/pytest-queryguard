"""BUG repeated-statement: a lookup per id inside ``begin_nested()`` blocks."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        found = []
        for i in range(1, 11):
            with session.begin_nested():
                found.append(
                    session.execute(
                        select(shop.Product).where(shop.Product.id == i)
                    ).scalar_one()
                )
        session.commit()
    assert len(found) == 10
