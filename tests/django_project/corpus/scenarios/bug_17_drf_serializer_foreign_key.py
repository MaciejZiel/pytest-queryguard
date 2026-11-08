"""BUG relationship-load: a DRF serializer reads ``author.name`` for each book.

``CharField(source="author.name")`` in a ``ListAPIView`` whose queryset does
not select the author: where most Django N+1s live.
"""

from rest_framework.test import APIRequestFactory

from ...library.views import BookList
from ..kit import BUG, REL

VERDICT = BUG
KINDS = frozenset({REL})


def run() -> None:
    response = BookList.as_view()(APIRequestFactory().get("/api/books/"))
    assert len(response.data) == 18
