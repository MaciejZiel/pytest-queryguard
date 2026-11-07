"""BUG relationship-load: each of several requests runs an N+1 of its own.

The counterpart of clean_36: counting each request on its own still finds
the loop inside the view (``book.author.name`` of every book).
"""

from django.test import Client

from ..kit import BUG, REL

VERDICT = BUG
KINDS = frozenset({REL})


def run() -> None:
    client = Client()
    for _ in range(3):
        assert len(client.get("/books/").json()["books"]) == 18
