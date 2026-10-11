"""The session-wide cache of catalog lookups and its invalidation."""

from __future__ import annotations

import gc
from collections.abc import Iterator
from typing import Any

import pytest

from pytest_queryguard import catalog
from pytest_queryguard.catalog import changes_catalog


@pytest.fixture(autouse=True)
def empty_cache() -> Iterator[None]:
    catalog.reset()
    yield
    catalog.reset()


class Engine:
    pass


class Conn:
    def __init__(self, engine: Engine | None = None) -> None:
        self.engine = engine or Engine()


def _filled(engine: Engine) -> None:
    catalog.for_engine(engine).leading[("public", "orders_idx")] = "tenant_id"


def _is_empty(engine: Engine) -> bool:
    return not catalog.for_engine(engine).leading


# ------------------------------------------------------------ classification


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE INDEX ON orders (customer_id)",
        "CREATE UNIQUE INDEX CONCURRENTLY o ON orders (a)",
        "DROP INDEX orders_idx",
        "ALTER INDEX orders_idx RENAME TO other_idx",
        "REINDEX INDEX orders_idx",
        "CREATE TABLE t (a int)",
        "create temp table t (a int) on commit drop",
        "DROP TABLE t",
        "ALTER TABLE orders ALTER COLUMN status TYPE text",
        "TRUNCATE TABLE orders",
        "TRUNCATE orders",
        "CREATE EXTENSION IF NOT EXISTS btree_gin",
        "DROP EXTENSION btree_gin",
        # anything else that is not known to leave the catalog alone
        "DO $$ BEGIN EXECUTE 'DROP INDEX orders_idx'; END $$",
        "CALL migrate()",
        "COMMENT ON TABLE orders IS 'x'",
        "GRANT SELECT ON orders TO someone",
        "DISCARD ALL",
        "EXPLAIN ANALYZE CREATE TABLE t AS SELECT 1",
        "PREPARE q AS SELECT 1",
        "REFRESH MATERIALIZED VIEW v",
        # after comments and parentheses
        "/* migration */ DROP INDEX orders_idx",
        "-- tag\nALTER TABLE orders ADD COLUMN x int",
        # a second statement after a safe one
        "SELECT 1; DROP INDEX orders_idx",
        "SELECT ';'; DROP INDEX orders_idx",
        "SET search_path = x; CREATE INDEX ON t (a)",
        "SELECT 1;; DROP INDEX orders_idx",
        # unreadable text
        "/* unterminated",
        "",
        "   ",
        "123",
    ],
)
def test_statements_that_may_change_the_catalog(sql: str) -> None:
    assert changes_catalog(sql) is True


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM orders WHERE id = %s",
        "select 1",
        "(SELECT 1) UNION (SELECT 2)",
        "/* app=shop */ SELECT 1",
        "WITH x AS (DELETE FROM t RETURNING a) SELECT * FROM x",
        "VALUES (1)",
        "TABLE orders",
        "INSERT INTO orders (id) VALUES (%s)",
        "UPDATE orders SET status = %s",
        "DELETE FROM orders",
        "MERGE INTO t USING s ON t.a = s.a WHEN MATCHED THEN DO NOTHING",
        "SHOW search_path",
        "SET LOCAL statement_timeout = '1s'",
        "RESET ALL",
        "BEGIN",
        "START TRANSACTION",
        "SAVEPOINT sa_1",
        "RELEASE SAVEPOINT sa_1",
        "COMMIT",
        "END",
        "ROLLBACK",
        "ROLLBACK TO SAVEPOINT sa_1",
        "ABORT",
        "ANALYZE orders",
        "VACUUM orders",
        "LOCK TABLE orders IN SHARE MODE",
        "COPY orders FROM STDIN",
        "DECLARE c CURSOR FOR SELECT 1",
        "FETCH 10 FROM c",
        "MOVE c",
        "CLOSE c",
        "EXECUTE q",
        "DEALLOCATE q",
        "LISTEN ch",
        "UNLISTEN ch",
        "NOTIFY ch",
        "CHECKPOINT",
        # semicolons that do not start another statement
        "SELECT 1;",
        "SELECT 1;  -- done\n",
        "SELECT 1;;",
        "SELECT 1; /* unterminated",
        "SELECT ';' AS a",
        "SELECT $$;DROP INDEX x$$",
        'SELECT 1 AS ";"',
        "SELECT 1 /* ; DROP INDEX x */",
    ],
)
def test_statements_that_leave_the_catalog_alone(sql: str) -> None:
    assert changes_catalog(sql) is False


def test_classification_is_linear_in_the_length() -> None:
    import time

    sql = "SELECT " + "'a;b', " * 20_000 + "1"
    start = time.perf_counter()
    assert changes_catalog(sql) is False
    assert time.perf_counter() - start < 1


# ------------------------------------------------------------------- the cache


def test_one_catalog_per_engine_for_the_whole_session() -> None:
    first, second = Engine(), Engine()
    assert catalog.for_engine(first) is catalog.for_engine(first)
    assert catalog.for_engine(first) is not catalog.for_engine(second)


def test_a_collected_engine_leaves_the_cache() -> None:
    engine = Engine()
    _filled(engine)
    assert catalog.cached_engines() == 1
    del engine
    gc.collect()
    assert catalog.cached_engines() == 0


def test_reset_empties_the_cache() -> None:
    engine = Engine()
    _filled(engine)
    catalog.reset()
    assert _is_empty(engine)


@pytest.mark.parametrize(
    "sql",
    ["SELECT 1", "INSERT INTO t VALUES (1)", "SET LOCAL a = 1", "COMMIT"],
)
def test_other_statements_keep_the_cache(sql: str) -> None:
    engine = Engine()
    _filled(engine)
    catalog.observe(Conn(engine), sql)
    assert not _is_empty(engine)


def test_ddl_on_any_engine_clears_the_whole_cache() -> None:
    engine, other = Engine(), Engine()
    _filled(engine)
    _filled(other)
    catalog.observe(Conn(), "DROP INDEX orders_idx")
    assert _is_empty(engine)
    assert _is_empty(other)


# A transaction that ran DDL changes what other connections see when it
# commits, and what its own connection sees when it rolls back. Entries read
# in the meantime are dropped then too.


@pytest.mark.parametrize("end", [catalog.on_commit, catalog.on_rollback])
def test_the_end_of_a_transaction_with_ddl_clears_the_cache(end: object) -> None:
    engine = Engine()
    conn = Conn(engine)
    catalog.observe(conn, "CREATE INDEX orders_idx ON orders (tenant_id, status)")
    _filled(engine)
    end(conn)  # type: ignore[operator]
    assert _is_empty(engine)
    # resolved: the next transaction on the connection did not run DDL
    _filled(engine)
    end(conn)  # type: ignore[operator]
    assert not _is_empty(engine)


def test_rollback_to_a_savepoint_after_ddl_clears_the_cache() -> None:
    engine = Engine()
    conn = Conn(engine)
    catalog.observe(conn, "DROP INDEX orders_idx")
    _filled(engine)
    catalog.on_rollback_savepoint(conn, "sa_1", None)
    assert _is_empty(engine)
    # the transaction, and its DDL, is still open
    _filled(engine)
    catalog.on_rollback(conn)
    assert _is_empty(engine)


def test_the_end_of_a_transaction_without_ddl_keeps_the_cache() -> None:
    engine = Engine()
    _filled(engine)
    catalog.observe(Conn(), "DROP INDEX orders_idx")  # another connection
    _filled(engine)
    conn = Conn(engine)
    catalog.on_commit(conn)
    catalog.on_rollback(conn)
    catalog.on_rollback_savepoint(conn, "sa_1", None)
    assert not _is_empty(engine)


@pytest.mark.parametrize(
    "sql", ["COMMIT", "END", "ROLLBACK", "ABORT", "ROLLBACK TO SAVEPOINT sa_1"]
)
def test_transaction_control_as_text_after_ddl_clears_the_cache(sql: str) -> None:
    engine = Engine()
    conn = Conn(engine)
    catalog.observe(conn, "DROP INDEX orders_idx")
    _filled(engine)
    catalog.observe(conn, sql)
    assert _is_empty(engine)


@pytest.mark.parametrize("sql", ["COMMIT PREPARED 'x'", "ROLLBACK PREPARED 'x'"])
def test_a_prepared_transaction_ending_clears_the_cache(sql: str) -> None:
    # Its DDL may have run on any connection, even in another process.
    engine = Engine()
    _filled(engine)
    catalog.observe(Conn(engine), sql)
    assert _is_empty(engine)


def test_a_connection_lost_with_ddl_pending_clears_the_cache() -> None:
    engine = Engine()
    conn = Conn(engine)
    catalog.observe(conn, "DROP INDEX orders_idx")
    _filled(engine)
    del conn
    gc.collect()
    assert _is_empty(engine)


def test_an_invalidated_connection_clears_the_cache_while_ddl_is_pending() -> None:
    engine = Engine()
    _filled(engine)
    catalog.on_invalidate(object(), object(), None)
    assert not _is_empty(engine)
    conn = Conn(engine)
    catalog.observe(conn, "DROP INDEX orders_idx")
    _filled(engine)
    catalog.on_invalidate(object(), object(), None)
    assert _is_empty(engine)


def test_an_engine_without_weak_references_gets_a_fresh_catalog() -> None:
    engine = object()
    catalog.for_engine(engine).leading[("public", "i")] = "a"
    assert catalog.for_engine(engine).leading == {}


# ------------------------------------------------- through SQLAlchemy's events


@pytest.fixture
def engine() -> Iterator[Any]:
    from sqlalchemy import create_engine

    from pytest_queryguard.listeners import install_index_check

    install_index_check()  # once per process, like the plugin does
    engine = create_engine("sqlite://")
    yield engine
    engine.dispose()


def test_ddl_outside_a_test_clears_the_cache(engine: Any) -> None:
    _filled(engine)
    with engine.connect() as conn:
        conn.exec_driver_sql("CREATE TABLE t (a int)")
        assert _is_empty(engine)
        _filled(engine)
        conn.commit()
    assert _is_empty(engine)


def test_statements_and_commits_without_ddl_keep_the_cache(engine: Any) -> None:
    _filled(engine)
    with engine.connect() as conn:
        conn.exec_driver_sql("SELECT 1")
        conn.commit()
        conn.exec_driver_sql("SELECT 2")
        conn.rollback()
    assert not _is_empty(engine)


def test_closing_a_connection_with_ddl_clears_the_cache(engine: Any) -> None:
    conn = engine.connect()
    conn.exec_driver_sql("CREATE TABLE t (a int)")
    _filled(engine)
    conn.close()
    assert _is_empty(engine)


def test_a_savepoint_rolled_back_after_ddl_clears_the_cache(engine: Any) -> None:
    with engine.connect() as conn:
        savepoint = conn.begin_nested()
        conn.exec_driver_sql("CREATE TABLE t (a int)")
        _filled(engine)
        savepoint.rollback()
        assert _is_empty(engine)


def test_an_invalidated_connection_with_ddl_clears_the_cache(engine: Any) -> None:
    with engine.connect() as conn:
        conn.exec_driver_sql("CREATE TABLE t (a int)")
        _filled(engine)
        conn.invalidate()
        assert _is_empty(engine)


def test_a_failing_transaction_listener_disables_the_plugin() -> None:
    from pytest_queryguard import listeners

    def boom(*args: object) -> None:
        raise RuntimeError("listener broke")

    try:
        listeners._guarded(boom)(object())
        assert repr(listeners.internal_error()) == "RuntimeError('listener broke')"
        # and the listeners do nothing afterwards
        listeners._guarded(boom)(object())
    finally:
        listeners._reset_for_tests()
