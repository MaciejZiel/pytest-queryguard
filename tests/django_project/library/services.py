from __future__ import annotations

import threading

from django.db import connection

from .models import Book


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
