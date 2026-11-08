"""BUG relationship-load: ``author.books.count()`` for every author.

A COUNT per author through the relation; prefetching (or an annotation) gives
the numbers with the authors.
"""

from ...library.models import Author
from ..kit import BUG, REL

VERDICT = BUG
KINDS = frozenset({REL})


def run() -> None:
    counts = [a.books.count() for a in Author.objects.all()]
    assert counts == [3] * 6
