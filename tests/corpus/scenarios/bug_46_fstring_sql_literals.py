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
_SEPARATE = (
    "four passes of the comprehension and a fifth run on a line of its own: a "
    "run whose call path no other run has is no pass of a loop, so the loop "
    "has four runs, below the repeat threshold of 5"
)
KNOWN = {
    "default": _SEPARATE,
    "write-loop": _SEPARATE,
    "column-load-2": _SEPARATE,
    "unused-eager-load": _SEPARATE,
    "unused-columns": _SEPARATE,
    "strict": _SEPARATE,
    "session-scope": _SEPARATE,
    "small-repeat": None,
    "all-opt-in": None,
}
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        ids = [
            session.execute(text(f"SELECT id FROM tag WHERE name = 'tag{i}'")).scalar()
            for i in range(1, 5)
        ] + [session.execute(text("SELECT id FROM tag WHERE name = 'tag1'")).scalar()]
    assert len(ids) == 5
