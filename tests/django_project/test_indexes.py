"""The missing-index check on Django statements, on the test database."""

from __future__ import annotations

import pytest
from django.db import connection

from .helpers import measure
from .library.models import Author, Book

pytestmark = [pytest.mark.allow_nplusone, pytest.mark.usefixtures("seeded")]


@pytest.fixture(autouse=True)
def _index_check() -> None:
    from pytest_queryguard import backends

    backends.install_index_check()


def test_a_filter_without_an_index_is_a_scan() -> None:
    with measure(index_check=True) as collector:
        list(Book.objects.filter(title="book 1.1"))
    assert [scan.issue.table for scan in collector.scans] == ["library_book"]
    (scan,) = collector.scans
    assert scan.callsite is not None
    assert scan.callsite.function == "test_a_filter_without_an_index_is_a_scan"


def test_a_foreign_key_has_its_index() -> None:
    with measure(index_check=True) as collector:
        list(Book.objects.filter(author_id=1))
        Author.objects.get(pk=2)
    assert collector.scans == []
    assert collector.explain_errors == []


def test_each_statement_is_explained_once() -> None:
    with measure(index_check=True) as collector:
        for title in ("book 1.1", "book 2.2", "book 3.0"):
            list(Book.objects.filter(title=title))
    assert len(collector.scans) == 1


def test_raw_sql_with_percent_signs() -> None:
    with measure(index_check=True) as collector, connection.cursor() as cursor:
        cursor.execute(
            "SELECT id FROM library_book WHERE title = %s AND body <> '%%'",
            ["book 1.1"],
        )
        cursor.fetchall()
    assert collector.explain_errors == []
    assert [scan.issue.table for scan in collector.scans] == ["library_book"]


def test_without_the_check_nothing_is_explained() -> None:
    with measure() as collector:
        list(Book.objects.filter(title="book 1.1"))
    assert collector.scans == []
