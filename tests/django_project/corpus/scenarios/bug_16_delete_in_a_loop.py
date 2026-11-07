"""BUG write-loop: ``review.delete()`` for each review of a list.

One DELETE per row; ``filter(pk__in=...).delete()`` deletes them together.
"""

from ...library.models import Review
from ..kit import BUG, WRITE

VERDICT = BUG
KINDS = frozenset({WRITE})


def run() -> None:
    for review in Review.objects.filter(stars__lte=2):
        review.delete()
