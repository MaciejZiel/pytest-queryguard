"""CLEAN: one book's author, tags and reviews: a few queries, no loop."""

from ...library.models import Book
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def run() -> None:
    book = Book.objects.get(pk=4)
    summary = (
        book.author.name,
        [t.name for t in book.tags.all()],
        book.reviews.count(),
    )
    assert summary[0] == "author 1"
