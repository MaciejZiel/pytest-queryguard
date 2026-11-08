"""CLEAN: titles and author names as values of one joined query."""

from ...library.models import Book
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def run() -> None:
    rows = list(Book.objects.values_list("title", "author__name"))
    assert len(rows) == 18
