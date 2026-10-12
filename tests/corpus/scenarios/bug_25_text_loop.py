"""BUG repeated-statement: ``text()`` SQL with a bound parameter, once per id."""

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        prices = [
            session.execute(
                text("SELECT price FROM product WHERE id = :id"), {"id": i}
            ).scalar_one()
            for i in range(1, 11)
        ]
    assert len(prices) == 10
