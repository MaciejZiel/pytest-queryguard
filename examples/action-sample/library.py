from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Book


def author_names(session: Session) -> list[str]:
    # The lazy load of Book.author in a loop is the N+1 the action reports.
    return [book.author.name for book in session.scalars(select(Book))]
