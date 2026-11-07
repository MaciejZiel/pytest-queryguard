"""BUG relationship-load: ``bookmark.target`` (a generic foreign key) per row.

The content types are selected with the bookmarks; each target is still
looked up on its own, one SELECT per bookmark.
"""

from django.contrib.contenttypes.models import ContentType

from ...library.models import Book, Bookmark
from ..kit import BUG, REL

VERDICT = BUG
KINDS = frozenset({REL})


def setup() -> None:
    book_type = ContentType.objects.get_for_model(Book)
    Bookmark.objects.bulk_create(
        [Bookmark(content_type=book_type, object_id=pk) for pk in range(1, 7)]
    )


def run() -> None:
    marks = Bookmark.objects.select_related("content_type")
    titles = [mark.target.title for mark in marks]
    assert len(titles) == 6
