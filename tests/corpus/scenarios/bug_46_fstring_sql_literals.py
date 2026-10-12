"""BUG repeated-statement: SQL built with an f-string, one string literal per call.

``text(f"... WHERE name = '{name}'")`` puts the value in the statement text. The
statements differ only in their literals, which the repeat rule normalizes.
(Also an injection risk: never do this with user input.)
"""

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        ids = [
            session.execute(text(f"SELECT id FROM tag WHERE name = 'tag{i}'")).scalar()
            for i in range(1, 5)
        ] + [session.execute(text("SELECT id FROM tag WHERE name = 'tag1'")).scalar()]
    assert len(ids) == 5
