"""BUG relationship-load: a nested DRF serializer lists each author's books.

``books = TitleSerializer(many=True)`` reads the reverse relation per author.
"""

from rest_framework.test import APIRequestFactory

from ...library.views import AuthorList
from ..kit import BUG, REL

VERDICT = BUG
KINDS = frozenset({REL})


def run() -> None:
    response = AuthorList.as_view()(APIRequestFactory().get("/api/authors/"))
    assert len(response.data) == 6
