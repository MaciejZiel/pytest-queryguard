"""CLEAN: the nested DRF serializer over authors that prefetch their books."""

from rest_framework.test import APIRequestFactory

from ...library.models import Author
from ...library.views import AuthorList
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


class Prefetched(AuthorList):
    queryset = Author.objects.prefetch_related("books")


def run() -> None:
    response = Prefetched.as_view()(APIRequestFactory().get("/api/authors/"))
    assert len(response.data) == 6
