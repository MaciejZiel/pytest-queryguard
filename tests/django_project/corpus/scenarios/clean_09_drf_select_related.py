"""CLEAN: the DRF list view whose queryset selects the author."""

from rest_framework.test import APIRequestFactory

from ...library.views import BookListFixed
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def run() -> None:
    response = BookListFixed.as_view()(APIRequestFactory().get("/api/books-fixed/"))
    assert len(response.data) == 18
