"""BUG relationship-load: an admin list column that reads ``obj.author.name``.

A ``list_display`` method is not a field, so the admin does not select the
author with the books; the change list page runs one SELECT per row.
"""

from django.contrib.auth.models import User
from django.test import Client

from ..kit import BUG, REL

VERDICT = BUG
KINDS = frozenset({REL})
CLIENT = Client()


def setup() -> None:
    user = User.objects.create_superuser("admin", "admin@example.com", "pw")
    CLIENT.force_login(user)


def run() -> None:
    response = CLIENT.get("/admin/library/book/")
    assert response.status_code == 200
    assert b"author 5" in response.content
