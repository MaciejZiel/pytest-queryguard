"""BUG column-load: ``.only("title")`` and then ``book.body`` for every book.

A field the query left out is loaded on its own, one SELECT per instance.
"""

from ...library.models import Book
from ..kit import BUG, COLUMN

VERDICT = BUG
KINDS = frozenset({COLUMN})


def run() -> None:
    sizes = [len(b.body) for b in Book.objects.only("title")]
    assert len(sizes) == 18
