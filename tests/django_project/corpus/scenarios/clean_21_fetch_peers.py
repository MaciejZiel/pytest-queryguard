"""CLEAN: Django 6.1's ``FETCH_PEERS`` loads every author of the page at once.

On older Django the scenario selects the authors instead.
"""

import django
from django.db import models

from ...library.models import Book
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def run() -> None:
    if django.VERSION >= (6, 1):
        books = Book.objects.fetch_mode(models.FETCH_PEERS)
    else:
        books = Book.objects.select_related("author")
    names = [b.author.name for b in books]
    assert len(names) == 18
