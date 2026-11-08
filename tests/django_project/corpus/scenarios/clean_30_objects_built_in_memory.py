"""CLEAN: relations set on new objects in memory are read without a query."""

from ...library.models import Author, Book
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def run() -> None:
    author = Author.objects.get(pk=1)
    books = [Book(title=f"draft {i}", author=author) for i in range(6)]
    assert {b.author.name for b in books} == {"author 0"}
    Book.objects.bulk_create(books)
