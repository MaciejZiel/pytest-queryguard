"""CLEAN: the books with their authors, selected in one query."""

from ...library.models import Book
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def run() -> None:
    lines = [
        f"{b.title} by {b.author.name}" for b in Book.objects.select_related("author")
    ]
    assert len(lines) == 18
