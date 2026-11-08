"""BUG relationship-load: ``.filter()`` on a prefetched relation queries again.

The prefetch fills ``author.books.all()``; a filter on it is a new query, one
per author. The prefetch should carry the filter (a ``Prefetch`` queryset).
"""

from ...library.models import Author
from ..kit import BUG, REL

VERDICT = BUG
KINDS = frozenset({REL})


def run() -> None:
    authors = Author.objects.prefetch_related("books")
    long_ones = {a.pk: list(a.books.filter(pages__gt=100)) for a in authors}
    assert all(len(books) == 2 for books in long_ones.values())
