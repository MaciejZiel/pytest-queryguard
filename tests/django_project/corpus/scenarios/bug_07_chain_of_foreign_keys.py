"""BUG relationship-load: ``book.author.publisher`` for every book.

Two N+1s at once: the author of each book, then the publisher of each author.
"""

from ...library.models import Book
from ..kit import BUG, REL

VERDICT = BUG
KINDS = frozenset({REL})


def run() -> None:
    names = {b.pk: b.author.publisher.name for b in Book.objects.all()}
    assert len(names) == 18
