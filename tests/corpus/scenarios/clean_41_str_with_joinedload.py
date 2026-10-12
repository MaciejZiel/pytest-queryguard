"""CLEAN: ``str(book)`` reads ``author``, which the query joined in."""

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..domains import library
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = library.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        stmt = select(library.Book).options(joinedload(library.Book.author))
        catalogue = "\n".join(str(b) for b in session.scalars(stmt))
    assert "by author1" in catalogue
