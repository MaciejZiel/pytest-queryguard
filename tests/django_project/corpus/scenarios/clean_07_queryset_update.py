"""CLEAN: one ``update()`` for many rows instead of a ``save()`` each."""

from django.db.models import F

from ...library.models import Book
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def run() -> None:
    assert Book.objects.filter(author_id__in=[1, 2]).update(pages=F("pages") + 1) == 6
