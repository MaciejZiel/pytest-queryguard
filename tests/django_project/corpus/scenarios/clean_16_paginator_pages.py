"""CLEAN: every page of a ``Paginator``, one query per page and a count.

Pagination repeats one SELECT with another OFFSET: no N+1.
"""

from django.core.paginator import Paginator

from ...library.models import Book
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def run() -> None:
    paginator = Paginator(Book.objects.order_by("id"), 3)
    titles = [
        b.title for number in paginator.page_range for b in paginator.page(number)
    ]
    assert len(titles) == 18
