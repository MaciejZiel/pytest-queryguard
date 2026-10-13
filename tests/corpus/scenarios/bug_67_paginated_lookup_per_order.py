"""BUG repeated-statement: a paginated, loop-invariant lookup run once per order.

A helper reads every tag in pages of two (keyset). It does not depend on the
order, yet it is called inside the loop over eight orders: the same walk, three
statements each, eight times. Reading the tags once, before the loop, is the fix.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
setup = shop.setup


def all_tags(session: Session) -> list[str]:
    names: list[str] = []
    last = 0
    while True:
        page = session.execute(
            select(shop.Tag.id, shop.Tag.name)
            .where(shop.Tag.id > last)
            .order_by(shop.Tag.id)
            .limit(2)
        ).all()
        if not page:
            return names
        names.extend(name for _, name in page)
        last = page[-1][0]


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        orders = session.scalars(
            select(shop.Order).order_by(shop.Order.id).limit(8)
        ).all()
        labels = {order.id: all_tags(session) for order in orders}
    assert all(len(tags) == 4 for tags in labels.values())
