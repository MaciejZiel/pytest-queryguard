"""CLEAN: several books by id with one ``in_bulk``."""

from ...library.models import Book
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def run() -> None:
    books = Book.objects.in_bulk([1, 3, 5, 7, 9, 11])
    assert len(books) == 6
