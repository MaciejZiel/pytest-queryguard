"""CLEAN: ``.only()`` with every field the loop reads."""

from ...library.models import Book
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def run() -> None:
    lines = [f"{b.title}: {b.pages}" for b in Book.objects.only("title", "pages")]
    assert len(lines) == 18
