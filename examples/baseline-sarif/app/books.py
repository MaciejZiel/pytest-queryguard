from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Author, Book


def titles_with_authors(session: Session) -> list[str]:
    """Legacy code with an N+1 that is recorded in the baseline."""
    titles = []
    for book in session.scalars(select(Book)).all():
        titles.append(f"{book.title} by {book.author.name}")
    return titles


def books_per_author(session: Session) -> dict[str, int]:
    """Legacy code with another N+1 that is recorded in the baseline."""
    counts = {}
    for author in session.scalars(select(Author)).all():
        counts[author.name] = len(author.books)
    return counts


def titles_of(session: Session, author: Author) -> list[str]:
    """New code with an N+1 that is not in the baseline."""
    titles = []
    for book in session.scalars(select(Book)).all():
        if book.author.id == author.id:
            titles.append(book.title)
    return titles
