"""BUG relationship-load: the tags of every book, one SELECT per book.

A many-to-many relation read for each row of the books query.
"""

from ...library.models import Book
from ..kit import BUG, REL

VERDICT = BUG
KINDS = frozenset({REL})


def run() -> None:
    tagged = {b.pk: sorted(t.name for t in b.tags.all()) for b in Book.objects.all()}
    assert all(len(names) == 2 for names in tagged.values())
