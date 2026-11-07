"""CLEAN: authors, their books and the books' tags in three queries."""

from ...library.models import Author
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def run() -> None:
    tags = {
        a.pk: {t.name for b in a.books.all() for t in b.tags.all()}
        for a in Author.objects.prefetch_related("books__tags")
    }
    assert len(tags) == 6
