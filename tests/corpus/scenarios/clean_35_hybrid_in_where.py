"""CLEAN: a hybrid property used on the SQL side, in ``WHERE``."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import library
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = library.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        long_books = session.scalars(select(library.Book).where(library.Book.is_long)).all()
        authors = session.scalars(select(library.Author).where(library.Author.has_books)).all()
    assert long_books and len(authors) == 6
