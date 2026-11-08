"""CLEAN: the admin change form of one book.

One object and its choices: a page full of queries, but no loop over rows.
"""

from django.contrib.auth.models import User
from django.test import Client

from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()
CLIENT = Client()


def setup() -> None:
    user = User.objects.create_superuser("admin", "admin@example.com", "pw")
    CLIENT.force_login(user)


def run() -> None:
    response = CLIENT.get("/admin/library/book/4/change/")
    assert response.status_code == 200
