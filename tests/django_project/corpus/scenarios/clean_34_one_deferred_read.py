"""CLEAN: the body of the one book a detail view shows, deferred in its query.

One extra query for one object is no loop.
"""

from ...library.models import Book
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def run() -> None:
    book = Book.objects.defer("body").get(pk=3)
    assert book.body.startswith("text")
