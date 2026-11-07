"""CLEAN: the admin change list with ``list_select_related = ("author",)``."""

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
    response = CLIENT.get("/admin/library/fastbook/")
    assert response.status_code == 200
    assert b"author 5" in response.content
