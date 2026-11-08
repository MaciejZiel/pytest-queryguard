"""CLEAN: authors with their books, prefetched: two queries for any number."""

from ...library.models import Author
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def run() -> None:
    shelf = {
        a.name: [b.title for b in a.books.all()]
        for a in Author.objects.prefetch_related("books")
    }
    assert len(shelf) == 6
