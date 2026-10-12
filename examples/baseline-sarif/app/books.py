from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Author, Book


def titles_with_authors(session: Session) -> list[str]:
    """Legacy code with an N+1 that is recorded in the baseline."""
    books = session.scalars(select(Book)).all()
    return [f"{book.title} by {book.author.name}" for book in books]


def authors_by_id(session: Session, ids: list[int]) -> list[Author]:
    """Legacy code with a loop of lookups that is recorded in the baseline."""
    return [session.get(Author, author_id) for author_id in ids]


def titles_of(session: Session, author: Author) -> list[str]:
    """New code with an N+1 that is not in the baseline."""
    books = session.scalars(select(Book)).all()
    return [book.title for book in books if book.author.id == author.id]
