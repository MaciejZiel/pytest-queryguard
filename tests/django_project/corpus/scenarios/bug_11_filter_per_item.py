"""BUG repeated-statement: one ``filter(author=...)`` query per author.

The same query shape for each author of a list, written as a separate
queryset instead of the relation: the repeat rule sees it.
"""

from ...library.models import Author, Book
from ..kit import BUG, REPEAT

VERDICT = BUG
KINDS = frozenset({REPEAT})


def run() -> None:
    authors = list(Author.objects.all())
    pages = {a.pk: sum(b.pages for b in Book.objects.filter(author=a)) for a in authors}
    assert len(pages) == 6
