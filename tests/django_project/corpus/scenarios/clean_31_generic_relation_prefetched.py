"""CLEAN: the bookmarks of every book, prefetched through the ``GenericRelation``."""

from django.contrib.contenttypes.models import ContentType

from ...library.models import Book, Bookmark
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def setup() -> None:
    book_type = ContentType.objects.get_for_model(Book)
    Bookmark.objects.bulk_create(
        [Bookmark(content_type=book_type, object_id=pk) for pk in (1, 1, 2, 5, 9)]
    )


def run() -> None:
    books = Book.objects.prefetch_related("bookmarks")
    marked = [b.title for b in books if b.bookmarks.exists()]
    assert len(marked) == 4
