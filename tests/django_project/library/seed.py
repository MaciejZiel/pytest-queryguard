"""Rows for the tests: 2 publishers, 6 authors, 18 books, tags, reviews."""

from __future__ import annotations

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
    publishers = [Publisher.objects.create(name=f"publisher {i}") for i in range(2)]
    tags = [Tag.objects.create(name=f"tag {i}") for i in range(4)]
    for i in range(AUTHORS):
        author = Author.objects.create(name=f"author {i}", publisher=publishers[i % 2])
        AuthorProfile.objects.create(author=author, bio=f"bio {i}")
        for j in range(BOOKS_PER_AUTHOR):
            book = Book.objects.create(
                title=f"book {i}.{j}", body="text " * 20, pages=100 + j, author=author
            )
            book.tags.add(tags[(i + j) % 4], tags[(i + j + 1) % 4])
            Review.objects.create(book=book, stars=1 + (i + j) % 5)
    for i in range(4):
        if i % 2:
            Restaurant.objects.create(name=f"restaurant {i}", serves_pizza=True)
        else:
            Place.objects.create(name=f"place {i}")
