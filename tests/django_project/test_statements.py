"""Statements through the execute wrapper: the events, budgets, repeats, write loops."""

from __future__ import annotations

import pytest
from django.db import connection, transaction

from pytest_queryguard import QueryBudgetExceeded, record_queries
from pytest_queryguard.detectors import WriteLoopFinding
from pytest_queryguard.events import ORM_DJANGO
from pytest_queryguard.messages import make_finding

from .helpers import detections, labels, measure
from .library.models import Author, Book, Publisher, Review

pytestmark = [pytest.mark.allow_nplusone, pytest.mark.usefixtures("seeded")]


def test_a_statement_is_recorded_with_its_call_site_and_time() -> None:
    with measure() as collector:
        names = list(
            Author.objects.filter(name="author 1").values_list("name", flat=True)
        )
    assert names == ["author 1"]
    (stmt,) = collector.statements
    assert stmt.sql.startswith("SELECT")
    assert "%s" in stmt.sql
    assert list(stmt.parameters) == ["author 1"]  # type: ignore[call-overload]
    assert stmt.orm == ORM_DJANGO
    assert stmt.callsite is not None
    assert stmt.callsite.filename == __file__
    assert (
        stmt.callsite.function
        == "test_a_statement_is_recorded_with_its_call_site_and_time"
    )
    assert stmt.duration is not None and stmt.duration >= 0
    assert collector.db_time > 0
    assert stmt.scope is not None


def test_executemany_is_one_statement_without_parameters() -> None:
    with measure() as collector, connection.cursor() as cursor:
        cursor.executemany(
            "UPDATE library_book SET pages = %s WHERE id = %s", [(1, 1), (2, 2)]
        )
    (stmt,) = collector.statements
    assert stmt.executemany
    assert stmt.parameters is None


def test_nothing_is_recorded_outside_a_measurement() -> None:
    with measure() as collector:
        pass
    Author.objects.count()
    assert collector.statements == []


def test_the_wrapper_comes_first_and_survives_a_user_wrapper() -> None:
    seen: list[str] = []

    def mine(execute, sql, params, many, context):  # type: ignore[no-untyped-def]
        seen.append(sql)
        return execute(sql, params, many, context)

    with measure() as collector:
        with connection.execute_wrapper(mine):
            Author.objects.count()
        Author.objects.count()
    assert len(seen) == 1
    assert len(collector.statements) == 2
    assert (
        connection.execute_wrappers[0].__module__ == "pytest_queryguard.django_backend"
    )


def test_a_get_in_a_loop_is_a_repeated_statement() -> None:
    with measure() as collector:
        for pk in range(1, 7):
            Book.objects.get(pk=pk)
    (finding,) = detections(collector)
    assert finding.count == 6
    assert ("repeated-statement",) == tuple(k for k, _ in labels(collector))
    text = make_finding(finding).message
    assert "in_bulk(ids)" in text


def test_savepoints_are_counted_but_never_repeat() -> None:
    with measure() as collector:
        for i in range(6):
            with transaction.atomic():
                Publisher.objects.create(name=f"p{i}")
    savepoints = [s for s in collector.statements if "SAVEPOINT" in s.sql.upper()]
    assert len(savepoints) >= 6
    assert detections(collector) == []


def test_save_in_a_loop_is_a_write_loop() -> None:
    with measure() as collector:
        for i in range(4):
            Publisher.objects.create(name=f"new {i}")
    (finding,) = detections(collector, write_loop_threshold=2)
    assert isinstance(finding, WriteLoopFinding)
    assert finding.count == 4
    assert finding.verb == "INSERT"
    assert "bulk_create(rows)" in make_finding(finding).message


def test_bulk_create_is_no_write_loop_even_in_batches_of_one() -> None:
    with measure() as collector:
        Publisher.objects.bulk_create(
            [Publisher(name=f"bulk {i}") for i in range(4)], batch_size=1
        )
    inserts = [s for s in collector.statements if s.sql.startswith("INSERT")]
    assert len(inserts) == 4
    assert len({s.flush for s in inserts}) == 1
    assert detections(collector, write_loop_threshold=2) == []


def test_bulk_update_in_batches_is_one_unit() -> None:
    books = list(Book.objects.all()[:4])
    for book in books:
        book.pages += 1
    with measure() as collector:
        Book.objects.bulk_update(books, ["pages"], batch_size=1)
    assert detections(collector, write_loop_threshold=2) == []


def test_a_cascade_delete_is_one_unit() -> None:
    with measure() as collector:
        Author.objects.filter(pk__in=[1, 2, 3]).delete()
    assert detections(collector, write_loop_threshold=2) == []


def test_delete_in_a_loop_is_a_write_loop() -> None:
    reviews = list(Review.objects.all()[:4])
    with measure() as collector:
        for review in reviews:
            review.delete()
    found = labels(collector, write_loop_threshold=2)
    assert [kind for kind, _ in found] == ["write-loop"]


def test_record_queries_sees_django() -> None:
    with record_queries() as log:
        Author.objects.count()
        Book.objects.count()
    assert log.count == 2
    assert all(q.sql.startswith("SELECT") for q in log)


def test_a_budget_fails_on_django_statements(queryguard) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(QueryBudgetExceeded), queryguard.max_queries(1):
        Author.objects.count()
        Book.objects.count()


def test_each_request_is_a_scope_of_its_own(client) -> None:  # type: ignore[no-untyped-def]
    with measure() as collector:
        for _ in range(2):
            assert client.get("/books/three/").status_code == 200
    gets = [s for s in collector.statements if "library_book" in s.sql]
    assert len(gets) == 6
    assert len({s.scope for s in gets}) == 2
    (finding,) = detections(collector)
    assert finding.count == 6
    assert "across 2 different requests or database connections" in (
        make_finding(finding).message
    )
    assert detections(collector, scope="session") == []


@pytest.mark.django_db(transaction=True)
def test_the_async_orm_has_the_awaiting_line_as_call_site() -> None:
    import asyncio

    async def titles() -> list[str]:
        books = [await Book.objects.aget(pk=pk) for pk in (1, 2, 3)]
        more = [b async for b in Book.objects.filter(author_id=1)]
        return [b.title for b in books + more]

    with measure() as collector:
        assert len(asyncio.run(titles())) == 6
    sites = {
        (s.callsite.function if s.callsite else None) for s in collector.statements
    }
    assert sites == {"titles"}
