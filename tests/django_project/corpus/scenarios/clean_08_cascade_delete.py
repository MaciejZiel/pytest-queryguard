"""CLEAN: one ``delete()`` whose cascade deletes books, reviews and tag links.

The cascade runs several DELETEs, but as one unit of work: no write loop.
"""

from ...library.models import Author
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def run() -> None:
    deleted, _ = Author.objects.filter(pk__in=[1, 2, 3]).delete()
    assert deleted > 3
