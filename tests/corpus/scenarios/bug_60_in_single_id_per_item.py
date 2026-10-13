"""BUG repeated-statement: an ``IN`` list of one id, once per item.

A helper takes a list of ids and is called with one id at a time from a loop:
the statement has an ``IN`` like a batch, but every list has one element.
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
setup = shop.setup


def names_for(session: Session, ids: Sequence[int]) -> list[str]:
    return list(
        session.scalars(select(shop.Product.name).where(shop.Product.id.in_(ids)))
    )


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        lines = session.scalars(select(shop.OrderLine).limit(10)).all()
        names = [names_for(session, [line.product_id])[0] for line in lines]
    assert len(names) == 10
