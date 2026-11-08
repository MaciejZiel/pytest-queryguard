"""BUG relationship-load: each author's ``books`` are read in a loop.

The reverse side of a foreign key: ``author.books.all()`` runs a SELECT per
author unless the authors' query prefetches the books.
"""

from ...library.models import Author
from ..kit import BUG, REL

VERDICT = BUG
KINDS = frozenset({REL})


def run() -> None:
    shelf = {a.name: [b.title for b in a.books.all()] for a in Author.objects.all()}
    assert len(shelf) == 6
