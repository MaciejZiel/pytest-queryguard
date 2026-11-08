"""BUG relationship-load: the bookmarks of each book, through a ``GenericRelation``.

The reverse side of a generic foreign key, read per book: one SELECT each.
"""

from django.contrib.contenttypes.models import ContentType

from ...library.models import Book, Bookmark
from ..kit import BUG, REL

VERDICT = BUG
KINDS = frozenset({REL})


def setup() -> None:
    book_type = ContentType.objects.get_for_model(Book)
    Bookmark.objects.bulk_create(
        [Bookmark(content_type=book_type, object_id=pk) for pk in (1, 1, 2, 5, 9)]
    )


def run() -> None:
    marked = [b.title for b in Book.objects.all() if b.bookmarks.exists()]
    assert len(marked) == 4
