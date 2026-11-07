"""CLEAN: several tags added to a book in one ``add()``."""

from ...library.models import Book, Tag
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def run() -> None:
    Book.objects.get(pk=1).tags.add(*Tag.objects.all())
