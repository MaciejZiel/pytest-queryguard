"""CLEAN: one ``.count()`` on a dynamic relationship."""

from sqlalchemy.orm import Session

from ..domains import library
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = library.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        author = session.get(library.Author, 1)
        count = author.books_dyn.count()
    assert count == 3
