"""CLEAN: ``undefer(Book.synopsis)`` in the query that needs the column."""

from sqlalchemy import select
from sqlalchemy.orm import Session, undefer

from ..domains import library
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = library.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        stmt = select(library.Book).options(undefer(library.Book.synopsis))
        export = [(b.title, b.synopsis) for b in session.scalars(stmt)]
    assert len(export) == 18
