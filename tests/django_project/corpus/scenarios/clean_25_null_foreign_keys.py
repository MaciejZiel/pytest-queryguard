"""CLEAN: reading a foreign key that is NULL runs no query."""

from ...library.models import Author
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def setup() -> None:
    Author.objects.update(publisher=None)


def run() -> None:
    publishers = [a.publisher for a in Author.objects.all()]
    assert publishers == [None] * 6
