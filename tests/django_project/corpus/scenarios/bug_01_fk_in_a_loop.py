"""BUG relationship-load: a list reads ``book.author`` for every book.

The classic foreign-key N+1: one SELECT for the books, then one per book for
its author, because nothing selected the authors with the books.
"""

from ...library.models import Book
from ..kit import BUG, REL

VERDICT = BUG
KINDS = frozenset({REL})


def book_lines() -> list[str]:
    return [f"{book.title} by {book.author.name}" for book in Book.objects.all()]


def run() -> None:
    assert len(book_lines()) == 18
