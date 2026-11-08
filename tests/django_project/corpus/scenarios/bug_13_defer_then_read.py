"""BUG column-load: ``.defer("body")`` and then ``book.body`` in a loop.

Deferring a field the code reads for every row costs a query per row.
"""

from ...library.models import Book
from ..kit import BUG, COLUMN

VERDICT = BUG
KINDS = frozenset({COLUMN})


def run() -> None:
    words = sum(len(b.body.split()) for b in Book.objects.defer("body"))
    assert words == 360
