"""CLEAN: the test calls a detail view once per book, through the test client.

Each request gets one book and reads its author: two statements, no loop in
the view. The loop is the test's, over separate requests (as tests of
permissions or URLs do); a request is counted on its own (seen on Read the
Docs, pretix and django-cms: the session, the user and the token looked up by
each request).
"""

from django.test import Client

from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def run() -> None:
    client = Client()
    for pk in range(1, 7):
        assert client.get(f"/books/{pk}/").json()["author"]
