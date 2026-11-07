"""CLEAN: two single lookups at two lines, each reading its author.

No loop: the two parents come from different query lines.
"""

from ...library.models import Book
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def run() -> None:
    first = Book.objects.get(pk=1)
    last = Book.objects.get(pk=18)
    assert first.author.name != last.author.name
