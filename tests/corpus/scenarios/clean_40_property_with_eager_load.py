"""CLEAN: the ``review_count`` property over reviews loaded with ``selectinload``."""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..domains import library
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = library.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        stmt = select(library.Book).options(selectinload(library.Book.reviews))
        total = sum(b.review_count for b in session.scalars(stmt))
    assert total == 36
