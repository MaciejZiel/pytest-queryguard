"""BUG relationship-load, write-loop: a recursive helper reads each created object's books.

Each depth of the recursion reads at the same line, through one more frame
of the helper; it is one walk, so its loads are one N+1. The objects are
created one by one (bulk_create sets no keys on MySQL): a write loop too.
"""

from ...library.models import Author, Book
from ..kit import BUG, REL, WRITE

VERDICT = BUG
KINDS = frozenset({REL, WRITE})


def titles(authors: list[Author]) -> list[str]:
    if not authors:
        return []
    book = authors[0].books.first()
    return [book.title if book else ""] + titles(authors[1:])


def run() -> None:
    authors = []
    for i in range(4):
        author = Author.objects.create(name=f"r{i}")
        Book.objects.create(title=f"t{i}", author=author)
        authors.append(author)
    assert titles(authors) == ["t0", "t1", "t2", "t3"]
