"""CLEAN: ``.defer("body")`` on a list that never reads the body."""

from ...library.models import Book
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def run() -> None:
    titles = [b.title for b in Book.objects.defer("body")]
    assert len(titles) == 18
