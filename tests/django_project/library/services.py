from __future__ import annotations

from .models import Book


def title_and_author(pk: int) -> str:
    """One book and its author: two queries per call, no loop."""
    book = Book.objects.get(pk=pk)
    return f"{book.title} by {book.author.name}"


def tag_counts(author_id: int) -> list[int]:
    """The tags of each book of an author, counted one book at a time."""
    return [book.tags.count() for book in Book.objects.filter(author_id=author_id)]
