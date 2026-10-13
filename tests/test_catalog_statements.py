"""Schema reflection is not counted by the repeat rules (issue 129)."""

from __future__ import annotations

import pytest
from sqlalchemy import (
    Column,
    Engine,
    Integer,
    MetaData,
    Table,
    create_engine,
    inspect,
)

from pytest_queryguard import QueryBudgetExceeded, max_queries, record_queries
from pytest_queryguard.collector import QueryCollector
from pytest_queryguard.detectors import detect, is_catalog_statement
from pytest_queryguard.events import CallSite, StatementEvent

# The tests run the reflection they describe; the outer plugin must not fail them.
pytestmark = pytest.mark.allow_nplusone

SITE = CallSite(filename="app.py", lineno=10, function="handler")


@pytest.mark.parametrize(
    "sql",
    [
        'PRAGMA main.table_info("t0")',
        "  pragma main.index_list(?)",
        "(PRAGMA foreign_key_list(?))",
        (
            "SELECT sql FROM (SELECT * FROM sqlite_master UNION ALL SELECT * FROM "
            "sqlite_temp_master) WHERE name = ? AND type IN (?)"
        ),
        "SELECT name FROM main.sqlite_master WHERE type = 'table'",
        'SELECT name FROM "sqlite_schema"',
        (
            "SELECT pg_catalog.pg_class.relname FROM pg_catalog.pg_class JOIN "
            "pg_catalog.pg_namespace ON pg_namespace.oid = pg_class.relnamespace"
        ),
        "SELECT table_name FROM information_schema.tables WHERE table_name = ?",
        'SELECT 1 FROM "information_schema"."columns"',
    ],
)
def test_catalog_statements_are_recognised(sql: str) -> None:
    assert is_catalog_statement(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM users WHERE id = ?",
        "SELECT * FROM pragmas WHERE id = ?",
        "SELECT * FROM sqlite_master_copy WHERE id = ?",
        "SELECT * FROM my_pg_catalog.things WHERE id = ?",
        "SELECT * FROM users WHERE name = ?",  # 'sqlite_master' was a literal
        "SELECT pragma FROM users",
        "SHOW CREATE TABLE users",
    ],
)
def test_other_statements_are_not_catalog_statements(sql: str) -> None:
    assert not is_catalog_statement(sql)


def _collector(*statements: tuple[str, int]) -> QueryCollector:
    c = QueryCollector()
    for sql, n in statements:
        c.statements.extend(
            StatementEvent(sql, SITE, None, parameters=(i,)) for i in range(n)
        )
    return c


def _detect(c: QueryCollector, small: int | None = None) -> list[object]:
    return list(
        detect(
            c,
            relationship_threshold=2,
            repeat_threshold=5,
            small_repeat_threshold=small,
        )
    )


def test_repeated_catalog_statements_are_not_a_repeat_finding() -> None:
    c = _collector(
        ('PRAGMA main.table_info("t")', 12), ("SELECT * FROM sqlite_master", 7)
    )
    assert _detect(c) == []


def test_small_repeat_does_not_count_them_either() -> None:
    c = _collector(('PRAGMA main.table_info("t")', 3))
    assert _detect(c, small=2) == []


def test_a_real_repeat_next_to_catalog_statements_is_still_reported() -> None:
    c = _collector(
        ('PRAGMA main.table_info("t")', 12),
        ("SELECT * FROM orders WHERE id = ?", 6),
    )
    (finding,) = _detect(c)
    assert finding.sql == "SELECT * FROM orders WHERE id = ?"  # type: ignore[attr-defined]


def _engine() -> Engine:
    engine = create_engine("sqlite://")
    md = MetaData()
    for i in range(5):
        Table(f"t{i}", md, Column("id", Integer, primary_key=True))
    md.create_all(engine)
    return engine


def test_metadata_reflect_and_a_fresh_inspector_are_not_n_plus_one() -> None:
    # the reproduction of the issue, on SQLite
    engine = _engine()
    with record_queries() as log:
        MetaData().reflect(engine)
        for _ in range(5):
            with engine.connect() as conn:
                assert inspect(conn).has_table("t0")
    assert log.count > 10  # the reflection ran
    log.assert_no_nplusone()


def test_budgets_still_count_catalog_statements() -> None:
    engine = _engine()
    with pytest.raises(QueryBudgetExceeded), max_queries(3):
        MetaData().reflect(engine)
