"""BUG relationship-load, write-loop: one helper per object a loop created.

The counterpart of clean_35: the objects come from no query, and the same
helper reads a relation of each of them from one line of a loop.
"""

from ...library.models import Author, Book
from ..kit import BUG, REL, WRITE

VERDICT = BUG
KINDS = frozenset({REL, WRITE})  # the creates in the loop, too


def latest_title(author: Author) -> str | None:
    book = author.books.order_by("-pk").first()
    return None if book is None else book.title


def run() -> None:
    authors = []
    for i in range(3):
        author = Author.objects.create(name=f"author {i}")
        Book.objects.create(title=f"book {i}", author=author)
        authors.append(author)
    titles = [latest_title(author) for author in authors]
    assert titles == ["book 0", "book 1", "book 2"]
