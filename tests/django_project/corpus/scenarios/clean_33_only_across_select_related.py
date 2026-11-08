"""CLEAN: ``.only()`` that names the related fields it selects with the books."""

from ...library.models import Book
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def run() -> None:
    books = Book.objects.select_related("author").only("title", "author__name")
    names = [f"{b.title} by {b.author.name}" for b in books]
    assert len(names) == 18
