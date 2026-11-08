"""CLEAN: a ``Prefetch`` with its own queryset and ``to_attr``."""

from django.db.models import Prefetch

from ...library.models import Author, Book
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def run() -> None:
    authors = Author.objects.prefetch_related(
        Prefetch(
            "books", queryset=Book.objects.filter(pages__gt=100), to_attr="long_books"
        )
    )
    assert all(len(a.long_books) == 2 for a in authors)
