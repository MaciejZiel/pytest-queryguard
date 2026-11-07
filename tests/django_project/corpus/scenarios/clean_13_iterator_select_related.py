"""CLEAN: ``iterator()`` over a queryset that selects the author."""

from ...library.models import Book
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def run() -> None:
    names = [
        b.author.name
        for b in Book.objects.select_related("author").iterator(chunk_size=4)
    ]
    assert len(names) == 18
