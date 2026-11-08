"""BUG repeated-statement: ``filter(...).exists()`` for every author of a list.

One EXISTS query per author; an annotation or one query over all authors
answers it at once.
"""

from ...library.models import Author, Book
from ..kit import BUG, REPEAT

VERDICT = BUG
KINDS = frozenset({REPEAT})


def run() -> None:
    authors = list(Author.objects.all())
    prolific = [
        a.name for a in authors if Book.objects.filter(author=a, pages__gt=101).exists()
    ]
    assert len(prolific) == 6
