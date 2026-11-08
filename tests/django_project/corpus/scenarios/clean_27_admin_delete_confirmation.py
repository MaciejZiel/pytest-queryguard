"""CLEAN: the admin's delete confirmation of an author, with its cascade.

The page lists every object the delete would remove (books, reviews, tag
links): the cascade collector reads them relation by relation, not per row.
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
    response = CLIENT.get("/admin/library/author/1/delete/")
    assert response.status_code == 200
