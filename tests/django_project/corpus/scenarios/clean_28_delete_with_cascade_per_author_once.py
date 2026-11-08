"""CLEAN: one author deleted with ``delete()``: its books and reviews cascade.

nplusone reported cascades like this as N+1s (its issue 47): one unit of work.
"""

from ...library.models import Author
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def run() -> None:
    _, by_model = Author.objects.get(pk=2).delete()
    assert by_model["library.Book"] == 3
