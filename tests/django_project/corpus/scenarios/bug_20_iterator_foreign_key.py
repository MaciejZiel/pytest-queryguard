"""BUG relationship-load: ``iterator()`` over books, reading each author.

Streaming the rows does not change the N+1: one SELECT per book's author.
"""

from ...library.models import Book
from ..kit import BUG, REL

VERDICT = BUG
KINDS = frozenset({REL})


def run() -> None:
    names = [b.author.name for b in Book.objects.iterator(chunk_size=4)]
    assert len(names) == 18
