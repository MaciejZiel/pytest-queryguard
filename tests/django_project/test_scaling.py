"""assert_queries_do_not_grow() with the Django backend."""

from __future__ import annotations

import asyncio

import pytest

from pytest_queryguard import (
    QueryBudgetExceeded,
    assert_queries_do_not_grow,
    assert_queries_do_not_grow_async,
)

from .library.models import Author, Book

pytestmark = pytest.mark.allow_nplusone


def make_books(count: int) -> None:
    Book.objects.all().delete()
    Author.objects.all().delete()
    authors = Author.objects.bulk_create(
        Author(name=f"author {i}") for i in range(count)
    )
    Book.objects.bulk_create(
        Book(title=f"book {i}", author=author) for i, author in enumerate(authors)
    )


def book_lines() -> list[str]:
    books = Book.objects.select_related("author")
    return [f"{book.title} by {book.author.name}" for book in books]


def book_lines_lazy() -> list[str]:
    return [f"{book.title} by {book.author.name}" for book in Book.objects.all()]


@pytest.mark.django_db
def test_select_related_passes() -> None:
    logs = assert_queries_do_not_grow(
        lambda _: book_lines(), sizes=(1, 10), setup=make_books
    )
    assert [log.count for log in logs.values()] == [1, 1]


@pytest.mark.django_db
def test_a_lazy_foreign_key_fails_with_the_django_fix() -> None:
    with pytest.raises(QueryBudgetExceeded) as info:
        assert_queries_do_not_grow(
            lambda _: book_lines_lazy(), sizes=(1, 10), setup=make_books
        )
    message = str(info.value)
    assert "2 at size 1, 11 at size 10" in message
    assert "ran 1 time at size 1, 10 times at size 10 (loads Book.author)" in message
    assert "in book_lines_lazy" in message
    assert '.select_related("author"), or .prefetch_related("author")' in message


@pytest.mark.django_db
def test_a_lookup_per_row_gets_the_django_repeat_fix() -> None:
    def names(_: None) -> list[str]:
        return [
            Author.objects.get(pk=pk).name
            for pk in Author.objects.values_list("pk", flat=True)
        ]

    with pytest.raises(QueryBudgetExceeded, match="in_bulk"):
        assert_queries_do_not_grow(names, sizes=(1, 5), setup=make_books)


@pytest.mark.django_db(transaction=True)
def test_async_with_django() -> None:
    from asgiref.sync import sync_to_async

    async def lines(_: None) -> list[str]:
        return [
            f"{book.title} by {book.author.name}"
            async for book in Book.objects.select_related("author")
        ]

    async def main() -> None:
        await assert_queries_do_not_grow_async(
            lines, sizes=(1, 10), setup=sync_to_async(make_books)
        )

    asyncio.run(main())
