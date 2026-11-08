"""CLEAN: ``book.author`` on the books of ``author.books``: already known.

Django sets the author on the books it loads through the relation, so no
lazy load runs for them.
"""

from ...library.models import Author
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def run() -> None:
    author = Author.objects.get(pk=2)
    names = [b.author.name for b in author.books.all()]
    assert names == ["author 1"] * 3
