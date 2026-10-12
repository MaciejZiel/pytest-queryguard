"""BUG repeated-statement: ``lazy="dynamic"`` collection filtered per author.

``author.books_dyn`` is an AppenderQuery; ``.count()`` on it runs a SELECT each
time, so a loop over authors is a query per author.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import library
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
setup = library.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        counts = [a.books_dyn.count() for a in session.scalars(select(library.Author))]
    assert counts == [3] * 6
