"""CLEAN: one helper reads a relation of two objects the test created, at two lines.

The objects come from no query, so their loads are grouped by where they
are read; the read is in one helper, but the helper is called from two lines
of the test: two reads, not a loop (read on Read the Docs: ``Project.save()``
reads ``self.versions`` for a project made in ``setUp`` and one made in the
test).
"""

from ...library.models import Author, Book
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def latest_title(author: Author) -> str | None:
    book = author.books.order_by("-pk").first()
    return None if book is None else book.title


def run() -> None:
    first = Author.objects.create(name="first")
    Book.objects.create(title="one", author=first)
    second = Author.objects.create(name="second")
    Book.objects.create(title="two", author=second)
    assert latest_title(first) == "one"
    assert latest_title(second) == "two"
