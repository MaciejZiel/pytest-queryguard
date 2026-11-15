"""CLEAN: one helper gets an object and reads its relation, called from two lines.

Each call gets one book with ``get()`` at the helper's line and reads its
author: the lookups share a query line, but the helper runs from two lines
of the application, once each, so there is no loop (seen on django-allauth:
one request records the session of its token and of its cookie through
``UserSession.objects.create_from_request()``, which reads the user of each).
"""

from ...library.models import Book
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def author_of(pk: int) -> str:
    return Book.objects.get(pk=pk).author.name


def handle() -> tuple[str, str]:
    by_token = author_of(1)
    by_cookie = author_of(7)
    return by_token, by_cookie


def run() -> None:
    assert all(handle())
