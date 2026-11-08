"""CLEAN: a transaction that creates an author with its books in bulk."""

from django.db import transaction

from ...library.models import Author, Book
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def run() -> None:
    with transaction.atomic():
        author = Author.objects.create(name="new author")
        Book.objects.bulk_create(
            [Book(title=f"new {i}", author=author) for i in range(5)]
        )
