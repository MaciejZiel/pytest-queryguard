"""BUG repeated-statement: ``refresh_from_db()`` for each object of a list.

Reloading objects one by one; one query with ``in_bulk`` reloads them all.
"""

from ...library.models import Book
from ..kit import BUG, REPEAT

VERDICT = BUG
KINDS = frozenset({REPEAT})


def run() -> None:
    books = list(Book.objects.all()[:6])
    for book in books:
        book.refresh_from_db()
