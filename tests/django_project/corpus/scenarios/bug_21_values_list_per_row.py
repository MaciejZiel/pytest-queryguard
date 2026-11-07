"""BUG relationship-load: ``author.books.values_list(...)`` for each author.

The relation is queried per author, even if only one column is wanted.
"""

from ...library.models import Author
from ..kit import BUG, REL

VERDICT = BUG
KINDS = frozenset({REL})


def run() -> None:
    titles = [
        list(a.books.values_list("title", flat=True)) for a in Author.objects.all()
    ]
    assert len(titles) == 6
