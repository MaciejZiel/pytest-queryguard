"""BUG relationship-load: a function view builds JSON with ``book.author.name``.

Called through the test client, as a project's view tests do.
"""

from django.test import Client

from ..kit import BUG, REL

VERDICT = BUG
KINDS = frozenset({REL})


def run() -> None:
    response = Client().get("/books/")
    assert len(response.json()["books"]) == 18
