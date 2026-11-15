from __future__ import annotations

import threading

from django.db import connection

from .models import Book, Publisher

LIMIT = 10


def add_publisher(name: str) -> Publisher:
    """A service with a limit: counts the publishers, then adds one."""
    if Publisher.objects.count() >= LIMIT:
        raise ValueError("too many publishers")
    return Publisher.objects.create(name=name)


def title_and_author(pk: int) -> str:
    """One book and its author: two queries per call, no loop."""
    book = Book.objects.get(pk=pk)
    return f"{book.title} by {book.author.name}"


def tag_counts(author_id: int) -> list[int]:
    """The tags of each book of an author, counted one book at a time."""
    return [book.tags.count() for book in Book.objects.filter(author_id=author_id)]


def place_order(pk: int) -> str:
    """A unit of work: looks its book up twice and reads the author."""
    book = Book.objects.get(pk=pk)
    Book.objects.get(pk=pk)
    return book.author.name


def titles_in_threads(pks: list[int]) -> list[str]:
    """A fan-out of the application: one thread, and one lookup, per book."""
    titles: dict[int, str] = {}

    def fetch(pk: int) -> None:
        try:
            titles[pk] = Book.objects.get(pk=pk).title
        finally:
            connection.close()

    threads = [threading.Thread(target=fetch, args=(pk,)) for pk in pks]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return [titles[pk] for pk in pks]
