"""Rows for the tests: 2 publishers, 6 authors, 18 books, tags, reviews.

The rows have explicit primary keys, the same on every database, so tests can
name them (``Book.objects.get(pk=1)``). Each test rolls its rows back, but a
PostgreSQL sequence does not go back: it is set past the seeded rows, so the
rows a test creates get new keys.
"""

from __future__ import annotations

from django.apps import apps
from django.core.management.color import no_style
from django.db import connection

from .models import (
    Author,
    AuthorProfile,
    Book,
    Place,
    Publisher,
    Restaurant,
    Review,
    Tag,
)

AUTHORS = 6
BOOKS_PER_AUTHOR = 3


def seed() -> None:
    publishers = [
        Publisher.objects.create(id=i + 1, name=f"publisher {i}") for i in range(2)
    ]
    tags = [Tag.objects.create(id=i + 1, name=f"tag {i}") for i in range(4)]
    book_id = review_id = 0
    for i in range(AUTHORS):
        author = Author.objects.create(
            id=i + 1, name=f"author {i}", publisher=publishers[i % 2]
        )
        AuthorProfile.objects.create(id=i + 1, author=author, bio=f"bio {i}")
        for j in range(BOOKS_PER_AUTHOR):
            book_id += 1
            book = Book.objects.create(
                id=book_id,
                title=f"book {i}.{j}",
                body="text " * 20,
                pages=100 + j,
                author=author,
            )
            book.tags.add(tags[(i + j) % 4], tags[(i + j + 1) % 4])
            review_id += 1
            Review.objects.create(id=review_id, book=book, stars=1 + (i + j) % 5)
    for i in range(4):
        if i % 2:
            Restaurant.objects.create(
                id=i + 1, name=f"restaurant {i}", serves_pizza=True
            )
        else:
            Place.objects.create(id=i + 1, name=f"place {i}")
    reset_sequences()


def reset_sequences() -> None:
    models = list(apps.get_app_config("library").get_models())
    statements = connection.ops.sequence_reset_sql(no_style(), models)
    if statements:
        with connection.cursor() as cursor:
            for sql in statements:
                cursor.execute(sql)
