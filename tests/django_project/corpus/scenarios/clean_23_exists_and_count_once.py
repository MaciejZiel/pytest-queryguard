"""CLEAN: an ``exists()`` and a ``count()`` of one queryset, once each."""

from ...library.models import Book
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def run() -> None:
    books = Book.objects.filter(pages__gt=100)
    assert books.exists()
    assert books.count() == 12
