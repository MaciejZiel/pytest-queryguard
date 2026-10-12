"""BUG column-load: a deferred column read for every row.

``Book.synopsis`` is deferred; the export reads it per book, one SELECT each.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import library
from ..kit import BUG, COLUMN, Ctx

VERDICT = BUG
KINDS = frozenset({COLUMN})
setup = library.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        export = [(b.title, b.synopsis) for b in session.scalars(select(library.Book))]
    assert len(export) == 18
