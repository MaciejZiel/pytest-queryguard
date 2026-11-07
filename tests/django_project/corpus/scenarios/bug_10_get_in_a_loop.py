"""BUG repeated-statement: ``Book.objects.get(pk=...)`` for every id of a list.

The same SELECT with another id, once per id: ``in_bulk(ids)`` or
``filter(pk__in=ids)`` fetches them in one query.
"""

from ...library.models import Book
from ..kit import BUG, REPEAT

VERDICT = BUG
KINDS = frozenset({REPEAT})


def run() -> None:
    titles = [Book.objects.get(pk=pk).title for pk in (1, 3, 5, 7, 9, 11)]
    assert len(titles) == 6
