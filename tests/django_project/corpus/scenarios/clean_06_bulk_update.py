"""CLEAN: changed rows written back with one ``bulk_update``."""

from ...library.models import Book
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def run() -> None:
    books = list(Book.objects.all())
    for book in books:
        book.pages += 1
    Book.objects.bulk_update(books, ["pages"], batch_size=5)
