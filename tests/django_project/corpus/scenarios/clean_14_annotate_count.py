"""CLEAN: the number of books per author as an annotation of one query."""

from django.db.models import Count

from ...library.models import Author
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def run() -> None:
    counts = [a.n for a in Author.objects.annotate(n=Count("books"))]
    assert counts == [3] * 6
