"""QueryLog.snapshot() with Django: views, querysets, writes and atomic blocks."""

from __future__ import annotations

import pytest
from django.db import transaction
from django.test import Client

from pytest_queryguard import QueryGuardFixture, record_queries

from .library.models import Author, Book

pytestmark = [pytest.mark.allow_nplusone, pytest.mark.usefixtures("seeded")]


def test_a_view_has_a_snapshot(client: Client) -> None:
    with record_queries() as log:
        assert client.get("/books/1/").status_code == 200
    assert log.snapshot() == [
        "SELECT ... FROM library_book WHERE ... LIMIT ...",
        "SELECT ... FROM library_author WHERE ... LIMIT ...",
    ]


def test_other_values_give_the_same_snapshot(client: Client) -> None:
    with record_queries() as first:
        client.get("/books/1/")
    with record_queries() as second:
        client.get("/books/7/")
    assert first.snapshot() == second.snapshot()
    with record_queries() as filtered:
        list(Book.objects.filter(title="book 0.0", pages__gte=100))
        list(Book.objects.filter(title="other", pages__gte=5))
    one, two = filtered.snapshot()
    assert one == two == "SELECT ... FROM library_book WHERE ... ORDER BY ..."


def test_joins_and_writes() -> None:
    with record_queries() as log:
        list(Book.objects.select_related("author").filter(author__name="author 1"))
        author = Author.objects.create(name="new")
        Author.objects.filter(pk=author.pk).update(name="renamed")
        Author.objects.filter(pk=author.pk).delete()
    snap = log.snapshot()
    assert snap[0] == (
        "SELECT ... FROM library_book INNER JOIN library_author ON ... "
        "WHERE ... ORDER BY ..."
    )
    assert snap[1].startswith("INSERT INTO library_author (...) VALUES (...)")
    assert "UPDATE library_author SET ... WHERE ..." in snap
    assert snap[-1] == "DELETE FROM library_author WHERE ..."


def test_atomic_savepoints_are_left_out() -> None:
    with record_queries() as log, transaction.atomic():
        Book.objects.filter(pk=1).update(pages=1)
    assert any(q.sql.startswith("SAVEPOINT") for q in log)  # inside the test's atomic
    assert log.snapshot() == ["UPDATE library_book SET ... WHERE ..."]


def test_the_fixture_has_the_snapshot_of_the_test(
    queryguard: QueryGuardFixture, client: Client
) -> None:
    client.get("/books/three/")
    assert (
        queryguard.snapshot()
        == [
            "SELECT ... FROM library_book WHERE ... LIMIT ...",
        ]
        * 3
    )
