"""BUG write-loop: ``book.save()`` after changing each book.

One UPDATE per book; ``bulk_update`` or a queryset ``update()`` does it in one.
"""

from django.db.models import F

from ...library.models import Book
from ..kit import BUG, WRITE

VERDICT = BUG
KINDS = frozenset({WRITE})


def run() -> None:
    books = list(Book.objects.filter(author_id=1))
    for book in books:
        book.pages = F("pages") + 1
        book.save(update_fields=["pages"])
