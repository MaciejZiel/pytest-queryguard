"""BUG column-load, relationship-load: ``.only("title")`` and then ``book.author``.

The foreign key column was left out too: each book loads ``author_id`` on its
own, then its author.
"""

from ...library.models import Book
from ..kit import BUG, COLUMN, REL

VERDICT = BUG
KINDS = frozenset({COLUMN, REL})
KNOWN = {
    "strict": (
        "strict mode raises at the second lazy load of Book.author, before "
        "the five column loads the column-load rule needs"
    )
}


def run() -> None:
    names = [b.author.name for b in Book.objects.only("title")]
    assert len(names) == 18
