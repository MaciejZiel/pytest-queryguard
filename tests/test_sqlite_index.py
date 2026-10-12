"""The SQLite missing-index check: plan analysis against real SQLite databases."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from pytest_queryguard import sqlite_index as si
from pytest_queryguard.events import ScanIssue

SCHEMA = """
CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, country TEXT);
CREATE TABLE orders (
    id INTEGER PRIMARY KEY, customer_id INTEGER, status TEXT, total INTEGER,
    note TEXT
);
CREATE TABLE tags (order_id INTEGER, tag TEXT);
CREATE TABLE lines (a INTEGER, b INTEGER, c INTEGER);
CREATE INDEX ix_lines_ab ON lines (a, b);
CREATE VIEW open_orders AS SELECT * FROM orders WHERE status = 'open';
"""


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    yield conn
    conn.close()


def issues(conn: sqlite3.Connection, sql: str, *params: object) -> list[ScanIssue]:
    cursor = conn.cursor()
    cursor.execute("EXPLAIN QUERY PLAN " + sql, params)
    rows = [si._row(r) for r in cursor.fetchall()]
    return si.analyse(rows, sql, si.Schema(cursor))


def fix(issue: ScanIssue) -> str | None:
    return None if issue.advice is None else issue.advice.create_sql


def one(conn: sqlite3.Connection, sql: str, *params: object) -> ScanIssue:
    found = issues(conn, sql, *params)
    assert len(found) == 1, found
    return found[0]


# ---------------------------------------------------------------- filters


def test_filter_without_an_index_is_a_scan(db: sqlite3.Connection) -> None:
    issue = one(db, "SELECT * FROM orders WHERE customer_id = ?", 1)
    assert (issue.table, issue.node, issue.kind) == ("orders", "SCAN", "filter")
    assert issue.column == "customer_id"
    assert fix(issue) == "CREATE INDEX ix_orders_customer_id ON orders (customer_id)"
    assert not issue.show_schema


def test_the_same_filter_with_an_index_is_not_reported(
    db: sqlite3.Connection,
) -> None:
    db.execute("CREATE INDEX ix_orders_customer ON orders (customer_id)")
    assert issues(db, "SELECT * FROM orders WHERE customer_id = ?", 1) == []


def test_primary_key_lookup_is_not_reported(db: sqlite3.Connection) -> None:
    assert issues(db, "SELECT * FROM orders WHERE id = ?", 1) == []


def test_select_without_where_is_not_reported(db: sqlite3.Connection) -> None:
    assert issues(db, "SELECT * FROM orders") == []
    assert issues(db, "SELECT count(*) FROM orders") == []


def test_order_by_without_an_index_is_not_a_filter(db: sqlite3.Connection) -> None:
    # SQLite sorts with a temp b-tree: the table is read in full, but nothing
    # narrows it, so no index on the filter is missing.
    assert issues(db, "SELECT * FROM orders ORDER BY total") == []
    assert issues(db, "SELECT * FROM orders ORDER BY total LIMIT 5") == []


def test_order_by_with_an_indexed_filter_is_not_reported(
    db: sqlite3.Connection,
) -> None:
    db.execute("CREATE INDEX ix_o ON orders (customer_id)")
    sql = "SELECT * FROM orders WHERE customer_id = ? ORDER BY total"
    assert issues(db, sql, 1) == []


def test_range_filter_is_reported_and_hints_the_column(
    db: sqlite3.Connection,
) -> None:
    issue = one(db, "SELECT * FROM orders WHERE total > ? AND total < ?", 1, 2)
    assert fix(issue) == "CREATE INDEX ix_orders_total ON orders (total)"


def test_between_is_a_range(db: sqlite3.Connection) -> None:
    issue = one(db, "SELECT * FROM orders WHERE total BETWEEN ? AND ?", 1, 2)
    assert issue.column == "total"


def test_equality_columns_come_first_in_a_composite_hint(
    db: sqlite3.Connection,
) -> None:
    sql = "SELECT * FROM orders WHERE total > ? AND status = ? AND customer_id = ?"
    issue = one(db, sql, 1, "x", 2)
    assert fix(issue) == (
        "CREATE INDEX ix_orders_status_customer_id_total "
        "ON orders (status, customer_id, total)"
    )


def test_is_null_is_a_filter(db: sqlite3.Connection) -> None:
    assert one(db, "SELECT * FROM orders WHERE note IS NULL").column == "note"


def test_is_not_null_and_not_equal_are_not_judged(db: sqlite3.Connection) -> None:
    assert issues(db, "SELECT * FROM orders WHERE note IS NOT NULL") == []
    assert issues(db, "SELECT * FROM orders WHERE status <> ?", "x") == []
    assert issues(db, "SELECT * FROM orders WHERE status != ?", "x") == []
    assert issues(db, "SELECT * FROM orders WHERE NOT status = ?", "x") == []


def test_in_list_is_an_equality_filter(db: sqlite3.Connection) -> None:
    sql = "SELECT * FROM orders WHERE customer_id IN (?, ?, ?)"
    issue = one(db, sql, 1, 2, 3)
    assert issue.column == "customer_id"
    db.execute("CREATE INDEX ix_o ON orders (customer_id)")
    assert issues(db, sql, 1, 2, 3) == []


def test_not_in_is_not_judged(db: sqlite3.Connection) -> None:
    assert (
        issues(db, "SELECT * FROM orders WHERE customer_id NOT IN (?, ?)", 1, 2) == []
    )


# ---------------------------------------------------------------- LIKE


def test_like_with_a_leading_wildcard_is_not_reported(db: sqlite3.Connection) -> None:
    # No btree index serves it, so "add an index" would be wrong advice.
    assert issues(db, "SELECT * FROM orders WHERE note LIKE ?", "%x%") == []
    assert issues(db, "SELECT * FROM orders WHERE note LIKE '%x'") == []


def test_like_does_not_count_next_to_an_equality(db: sqlite3.Connection) -> None:
    sql = "SELECT * FROM orders WHERE note LIKE ? AND status = ?"
    issue = one(db, sql, "%x%", "open")
    assert issue.column == "status"


def test_glob_is_not_judged(db: sqlite3.Connection) -> None:
    assert issues(db, "SELECT * FROM orders WHERE note GLOB ?", "a*") == []


# ---------------------------------------------------------------- OR, functions


def test_or_with_one_unindexed_branch_hints_only_that_column(
    db: sqlite3.Connection,
) -> None:
    db.execute("CREATE INDEX ix_o ON orders (customer_id)")
    issue = one(db, "SELECT * FROM orders WHERE customer_id = ? OR status = ?", 1, "x")
    assert fix(issue) == "CREATE INDEX ix_orders_status ON orders (status)"


def test_or_with_both_branches_indexed_is_not_reported(db: sqlite3.Connection) -> None:
    db.execute("CREATE INDEX ix_o ON orders (customer_id)")
    db.execute("CREATE INDEX ix_s ON orders (status)")
    sql = "SELECT * FROM orders WHERE customer_id = ? OR status = ?"
    assert issues(db, sql, 1, "x") == []


def test_or_with_two_unindexed_branches_gets_a_generic_hint(
    db: sqlite3.Connection,
) -> None:
    issue = one(db, "SELECT * FROM orders WHERE total = ? OR status = ?", 1, "x")
    assert issue.column is None
    assert issue.advice is None and issue.column is None and not issue.expression


def test_function_on_the_column_asks_for_an_expression_index(
    db: sqlite3.Connection,
) -> None:
    db.execute("CREATE INDEX ix_c ON customers (name)")
    issue = one(db, "SELECT * FROM customers WHERE lower(name) = ?", "a")
    assert issue.expression
    assert issue.column is None
    assert issue.advice is None and issue.expression


def test_arithmetic_on_the_column_is_not_judged(db: sqlite3.Connection) -> None:
    assert issues(db, "SELECT * FROM orders WHERE total + 1 = ?", 3) == []


# ---------------------------------------------------------------- joins


def test_join_on_a_column_without_an_index_is_reported(
    db: sqlite3.Connection,
) -> None:
    db.execute("PRAGMA automatic_index = OFF")
    sql = "SELECT * FROM tags t JOIN orders o ON o.note = t.tag"
    plan = [r[3] for r in db.execute("EXPLAIN QUERY PLAN " + sql)]
    assert [d.split()[0] for d in plan] == ["SCAN", "SCAN"]
    issue = one(db, sql)  # the inner table; the one the join starts from is fine
    assert issue.kind == "join condition"
    assert (issue.table, issue.column) in {("orders", "note"), ("tags", "tag")}


def test_join_with_an_index_is_not_reported(db: sqlite3.Connection) -> None:
    db.execute("CREATE INDEX ix_o ON orders (customer_id)")
    sql = "SELECT * FROM customers c JOIN orders o ON o.customer_id = c.id"
    assert issues(db, sql) == []


def test_join_starting_table_is_not_reported_for_its_join_column(
    db: sqlite3.Connection,
) -> None:
    db.execute("CREATE INDEX ix_o ON orders (customer_id)")
    # customers is read in full, and nothing narrows it: that is the plan.
    sql = "SELECT * FROM customers c JOIN orders o ON o.customer_id = c.id"
    plan = [r[3] for r in db.execute("EXPLAIN QUERY PLAN " + sql)]
    assert any(d.startswith("SCAN") for d in plan)
    assert issues(db, sql) == []


def test_automatic_index_is_a_missing_index(db: sqlite3.Connection) -> None:
    sql = "SELECT * FROM tags t JOIN orders o ON o.customer_id = t.order_id"
    plan = [r[3] for r in db.execute("EXPLAIN QUERY PLAN " + sql)]
    assert any("AUTOMATIC" in d for d in plan), plan
    issue = one(db, sql)  # tags is the starting table: a scan is the plan
    assert issue.table == "orders"
    assert issue.node.startswith("SEARCH USING AUTOMATIC")
    assert issue.kind == "join condition"
    assert fix(issue) == "CREATE INDEX ix_orders_customer_id ON orders (customer_id)"


def test_automatic_index_is_gone_once_the_column_is_indexed(
    db: sqlite3.Connection,
) -> None:
    db.execute("CREATE INDEX ix_o ON orders (customer_id)")
    sql = "SELECT * FROM tags t JOIN orders o ON o.customer_id = t.order_id"
    assert issues(db, sql) == []


def test_filter_on_the_joined_table_is_reported(db: sqlite3.Connection) -> None:
    db.execute("CREATE INDEX ix_o ON orders (customer_id)")
    sql = (
        "SELECT * FROM orders o JOIN customers c ON c.id = o.customer_id "
        "WHERE o.status = ?"
    )
    issue = one(db, sql, "open")
    assert (issue.table, issue.column) == ("orders", "status")


def test_filter_on_the_table_a_join_starts_from_is_reported(
    db: sqlite3.Connection,
) -> None:
    db.execute("PRAGMA automatic_index = OFF")
    sql = (
        "SELECT * FROM orders o JOIN tags t ON t.order_id = o.id "
        "WHERE o.status = ? AND t.tag = ?"
    )
    found = issues(db, sql, "open", "x")
    # SQLite starts from tags and looks orders up by its primary key.
    assert [(i.table, i.column) for i in found] == [("tags", "tag")]


def test_self_join_aliases_are_told_apart(db: sqlite3.Connection) -> None:
    sql = (
        "SELECT * FROM customers a JOIN customers b ON a.id = b.id WHERE a.country = ?"
    )
    issue = one(db, sql, "x")
    assert issue.column == "country"


def test_columns_of_one_table_compared_to_each_other_are_not_judged(
    db: sqlite3.Connection,
) -> None:
    assert issues(db, "SELECT * FROM orders WHERE total = customer_id") == []


# ---------------------------------------------------------------- nesting


def test_subquery_is_checked_with_its_own_filter(db: sqlite3.Connection) -> None:
    sql = (
        "SELECT * FROM customers WHERE id IN "
        "(SELECT customer_id FROM orders WHERE status = ?)"
    )
    issue = one(db, sql, "open")
    assert (issue.table, issue.column) == ("orders", "status")


def test_correlated_subquery_reports_the_inner_table(db: sqlite3.Connection) -> None:
    sql = (
        "SELECT * FROM customers c WHERE EXISTS "
        "(SELECT 1 FROM orders o WHERE o.customer_id = c.id)"
    )
    issue = one(db, sql)
    assert (issue.table, issue.column) == ("orders", "customer_id")
    db.execute("CREATE INDEX ix_o ON orders (customer_id)")
    assert issues(db, sql) == []


def test_outer_table_of_a_correlated_subquery_is_not_blamed(
    db: sqlite3.Connection,
) -> None:
    db.execute("CREATE INDEX ix_o ON orders (customer_id)")
    sql = (
        "SELECT * FROM customers c WHERE EXISTS "
        "(SELECT 1 FROM orders o WHERE o.customer_id = c.id)"
    )
    assert issues(db, sql) == []


def test_cte_is_checked_on_the_table_behind_it(db: sqlite3.Connection) -> None:
    sql = "WITH c AS (SELECT * FROM orders WHERE status = ?) SELECT * FROM c"
    assert one(db, sql, "x").table == "orders"


def test_cte_named_like_a_table_is_not_the_table(db: sqlite3.Connection) -> None:
    sql = "WITH tags AS (SELECT 1 AS x) SELECT * FROM tags WHERE x = ?"
    assert issues(db, sql, 1) == []


def test_subquery_alias_named_like_a_table_is_not_the_table(
    db: sqlite3.Connection,
) -> None:
    sql = "SELECT * FROM (SELECT 1 AS x) AS tags WHERE x = ?"
    assert issues(db, sql, 1) == []


def test_compound_select_gives_one_generic_finding_per_table(
    db: sqlite3.Connection,
) -> None:
    sql = (
        "SELECT id FROM orders WHERE status = ? UNION "
        "SELECT id FROM orders WHERE total = ?"
    )
    issue = one(db, sql, "a", 1)
    assert issue.table == "orders"
    assert issue.advice is None and issue.column is None and not issue.expression


def test_tables_behind_a_view_are_not_judged(db: sqlite3.Connection) -> None:
    # The SQL names the view, not the table: the filter cannot be placed.
    assert issues(db, "SELECT * FROM open_orders WHERE total = ?", 1) == []


def test_view_filter_alone_is_not_a_scan_of_a_filtered_column(
    db: sqlite3.Connection,
) -> None:
    # The only filter is the one inside the view, which is not in the SQL.
    assert issues(db, "SELECT * FROM open_orders") == []


# ---------------------------------------------------------------- indexes


def test_composite_index_with_the_leading_column_missing(
    db: sqlite3.Connection,
) -> None:
    issue = one(db, "SELECT * FROM lines WHERE b = ?", 1)
    assert fix(issue) == "CREATE INDEX ix_lines_b ON lines (b)"


def test_composite_index_with_its_leading_column_is_not_reported(
    db: sqlite3.Connection,
) -> None:
    assert issues(db, "SELECT * FROM lines WHERE a = ?", 1) == []
    assert issues(db, "SELECT * FROM lines WHERE a = ? AND b = ?", 1, 2) == []


def test_covering_index_scan_with_a_filter_on_a_trailing_column(
    db: sqlite3.Connection,
) -> None:
    sql = "SELECT b FROM lines WHERE b = ?"
    plan = [r[3] for r in db.execute("EXPLAIN QUERY PLAN " + sql, (1,))]
    assert plan == ["SCAN lines USING COVERING INDEX ix_lines_ab"]
    issue = one(db, sql, 1)
    assert issue.node == "SCAN COVERING INDEX ix_lines_ab"
    assert fix(issue) == "CREATE INDEX ix_lines_b ON lines (b)"


def test_covering_index_scan_without_a_filter_is_not_reported(
    db: sqlite3.Connection,
) -> None:
    assert issues(db, "SELECT a, b FROM lines") == []
    assert issues(db, "SELECT a, b FROM lines ORDER BY a") == []


def test_index_scan_for_a_range_filter_is_not_judged(db: sqlite3.Connection) -> None:
    # A whole index walked for a range predicate: pagination does that on purpose.
    assert issues(db, "SELECT b FROM lines WHERE b > ?", 1) == []


def test_partial_index_follows_the_bound_value(db: sqlite3.Connection) -> None:
    db.execute("CREATE INDEX ix_open ON orders (status) WHERE status = 'open'")
    sql = "SELECT * FROM orders WHERE status = ?"
    # SQLite plans with the bound value: 'open' meets the index predicate.
    assert issues(db, sql, "open") == []
    assert one(db, sql, "closed").table == "orders"


def test_partial_index_serves_its_own_literal(db: sqlite3.Connection) -> None:
    db.execute("CREATE INDEX ix_open ON orders (status) WHERE status = 'open'")
    assert issues(db, "SELECT * FROM orders WHERE status = 'open'") == []


def test_unique_constraint_index_counts(db: sqlite3.Connection) -> None:
    db.execute("CREATE TABLE u (id INTEGER PRIMARY KEY, email TEXT UNIQUE, n INT)")
    assert issues(db, "SELECT * FROM u WHERE email = ?", "x") == []
    assert one(db, "SELECT * FROM u WHERE n = ?", 1).column == "n"


def test_without_rowid_primary_key_counts(db: sqlite3.Connection) -> None:
    db.execute("CREATE TABLE w (k TEXT PRIMARY KEY, v INT) WITHOUT ROWID")
    assert issues(db, "SELECT * FROM w WHERE k = ?", "x") == []
    assert one(db, "SELECT * FROM w WHERE v = ?", 1).column == "v"


def test_filter_on_a_column_that_already_leads_an_index_is_left_alone(
    db: sqlite3.Connection,
) -> None:
    # A scan although the column leads an index (a collation or affinity
    # mismatch, or ANALYZE statistics): a CREATE INDEX hint would duplicate it.
    db.execute("CREATE TABLE ci (name TEXT COLLATE NOCASE)")
    db.execute("CREATE INDEX ix_ci ON ci (name COLLATE BINARY)")
    sql = "SELECT * FROM ci WHERE name = ?"
    plan = [r[3] for r in db.execute("EXPLAIN QUERY PLAN " + sql, ("x",))]
    assert plan == ["SCAN ci"]
    assert issues(db, sql, "x") == []


# ---------------------------------------------------------------- statements


def test_update_and_delete_filters_are_checked(db: sqlite3.Connection) -> None:
    assert (
        one(db, "UPDATE orders SET total = 1 WHERE status = ?", "x").table == "orders"
    )
    assert one(db, "DELETE FROM orders WHERE status = ?", "x").table == "orders"
    assert issues(db, "UPDATE orders SET status = 'x' WHERE id = ?", 1) == []
    assert issues(db, "UPDATE orders SET status = ?", "x") == []
    assert issues(db, "DELETE FROM orders") == []


def test_set_clause_is_not_a_filter(db: sqlite3.Connection) -> None:
    assert issues(db, "UPDATE orders SET status = ?, total = ?", "x", 1) == []


def test_with_update_is_checked(db: sqlite3.Connection) -> None:
    sql = "WITH x AS (SELECT 1) UPDATE orders SET total = 1 WHERE status = ?"
    assert one(db, sql, "x").table == "orders"


def test_internal_tables_and_constant_rows_are_skipped(db: sqlite3.Connection) -> None:
    assert issues(db, "SELECT * FROM sqlite_master WHERE name = ?", "x") == []
    assert issues(db, "SELECT 1 WHERE 1 = ?", 1) == []
    assert (
        issues(db, "SELECT * FROM pragma_table_info('orders') WHERE name = ?", "x")
        == []
    )


def test_virtual_table_is_skipped(db: sqlite3.Connection) -> None:
    try:
        db.execute("CREATE VIRTUAL TABLE docs USING fts5(body)")
    except sqlite3.OperationalError:
        pytest.skip("this SQLite has no FTS5")
    assert issues(db, "SELECT * FROM docs WHERE body MATCH ?", "x") == []
    assert issues(db, "SELECT * FROM docs WHERE rowid = ?", 1) == []


def test_unknown_table_names_are_skipped(db: sqlite3.Connection) -> None:
    rows = [(1, 0, 0, "SCAN ghost")]
    assert (
        si.analyse(rows, "SELECT * FROM ghost WHERE x = ?", si.Schema(db.cursor()))
        == []
    )


def test_unknown_plan_shapes_give_nothing(db: sqlite3.Connection) -> None:
    schema = si.Schema(db.cursor())
    sql = "SELECT * FROM orders WHERE status = ?"
    for detail in (
        "SCAN",
        "SCAN orders VIRTUAL TABLE INDEX 3:",
        "SCAN orders USING SOMETHING NEW",
        "SEARCH orders USING INDEX ix (status=?)",
        "MULTI-INDEX OR",
        "USE TEMP B-TREE FOR ORDER BY",
        "COMPOUND QUERY",
        "SOMETHING FROM A FUTURE VERSION",
    ):
        assert si.analyse([(1, 0, 0, detail)], sql, schema) == [], detail
    assert si.analyse([(1, 0, 0, None)], sql, schema) == []
    assert si.analyse([(1,)], sql, schema) == []
    assert si.analyse([("x", "y", 0, "SCAN orders")], sql, schema) == []


def test_older_sqlite_plan_text(db: sqlite3.Connection) -> None:
    # Before SQLite 3.36 the plan said SCAN TABLE, and named the alias with AS.
    schema = si.Schema(db.cursor())
    sql = "SELECT * FROM orders o WHERE o.status = ?"
    found = si.analyse([(2, 0, 0, "SCAN TABLE orders AS o")], sql, schema)
    assert [i.table for i in found] == ["orders"]
    plain = "SELECT * FROM orders WHERE status = ?"
    found = si.analyse([(2, 0, 0, "SCAN TABLE orders")], plain, schema)
    assert [i.table for i in found] == ["orders"]
    auto = [(2, 0, 0, "SEARCH TABLE orders USING AUTOMATIC COVERING INDEX (status=?)")]
    assert [i.column for i in si.analyse(auto, plain, schema)] == ["status"]


def test_automatic_index_with_unreadable_columns_gets_a_generic_hint(
    db: sqlite3.Connection,
) -> None:
    schema = si.Schema(db.cursor())
    rows = [(2, 0, 0, "SEARCH orders USING AUTOMATIC INDEX (lower(status)=?)")]
    found = si.analyse(rows, "SELECT * FROM orders", schema)
    assert found[0].advice is None and found[0].column is None


def test_quoted_and_mixed_case_names(db: sqlite3.Connection) -> None:
    db.execute('CREATE TABLE "Mixed Case" ("Some Col" INT, other INT)')
    issue = one(db, 'SELECT * FROM "mixed case" WHERE "some col" = ?', 1)
    assert issue.table == "Mixed Case"
    assert fix(issue) == (
        'CREATE INDEX ix_Mixed_Case_Some_Col ON "Mixed Case" ("Some Col")'
    )


def test_statement_with_comments_and_strings(db: sqlite3.Connection) -> None:
    sql = (
        "/* WHERE total = 1 */ SELECT 'WHERE x = 1' AS s, * FROM orders -- WHERE a = 1\n"
        "WHERE status = ?"
    )
    assert one(db, sql, "x").column == "status"
    assert issues(db, "SELECT 'WHERE status = 1' FROM orders") == []


def test_row_factories_do_not_break_the_check(db: sqlite3.Connection) -> None:
    db.row_factory = sqlite3.Row
    assert one(db, "SELECT * FROM orders WHERE status = ?", "x").table == "orders"
    db.row_factory = lambda cursor, row: {
        c[0]: v for c, v in zip(cursor.description, row, strict=True)
    }
    assert one(db, "SELECT * FROM orders WHERE status = ?", "x").table == "orders"


def test_table_in_a_file_database(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "app.db")
    conn.executescript(SCHEMA)
    try:
        assert one(conn, "SELECT * FROM orders WHERE status = ?", "x").table == "orders"
    finally:
        conn.close()


def test_analyze_statistics_can_make_a_scan_the_plan() -> None:
    # Documented limit: with statistics that say the table is tiny, SQLite
    # prefers a scan over an index it has, and the check sees only the plan.
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE s (id INTEGER PRIMARY KEY, k INT);"
        "CREATE INDEX ix_s ON s (k);"
        "INSERT INTO s (k) VALUES (1), (1), (1);"
        "ANALYZE;"
    )
    sql = "SELECT * FROM s WHERE k = ?"
    # Whatever the plan, k leads an index, so it is not reported as missing.
    assert issues(conn, sql, 1) == []
    # A column without an index is still reported after ANALYZE.
    conn.execute("ALTER TABLE s ADD COLUMN v INT")
    assert [i.column for i in issues(conn, "SELECT * FROM s WHERE v = ?", 1)] == ["v"]
    conn.close()


# ---------------------------------------------------------------- the tokenizer


def test_is_checked_statement() -> None:
    for sql in (
        "SELECT 1",
        " select 1",
        "/* tag */ UPDATE t SET a = 1",
        "-- tag\nDELETE FROM t",
        "WITH x AS (SELECT 1) SELECT * FROM x",
        "(SELECT 1)",
    ):
        assert si.is_checked_statement(sql), sql
    for sql in ("INSERT INTO t VALUES (1)", "PRAGMA table_info(t)", "BEGIN", ""):
        assert not si.is_checked_statement(sql), sql


def test_tokenizer_unquotes_identifiers_and_keeps_offsets() -> None:
    sql = 'SELECT "a""b", `c`, [d e] FROM t WHERE x = :p'
    tokens = si.tokenize(sql)
    assert [t.text for t in tokens if t.kind == "qid"] == ['a"b', "c", "d e"]
    assert tokens[-1].kind == "param"
    assert sql[tokens[-1].start : tokens[-1].end] == ":p"


@pytest.mark.parametrize("seed", range(40))
def test_analyse_survives_random_statements(seed: int) -> None:
    import random

    rng = random.Random(seed)
    words = [
        "SELECT", "FROM", "WHERE", "AND", "OR", "ON", "JOIN", "(", ")", ",", ".",
        "=", "<", "IN", "IS", "NOT", "NULL", "?", "orders", "o", "status", "id",
        "lower", "'x'", "1", "AS", "BETWEEN", "LIKE", "UNION", "WITH", "SET",
    ]  # fmt: skip
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    schema = si.Schema(conn.cursor())
    rows = [
        (1, 0, 0, "SCAN orders"),
        (2, 0, 0, "SCAN o"),
        (3, 1, 0, "SEARCH orders USING AUTOMATIC COVERING INDEX (status=?)"),
    ]
    for _ in range(25):
        sql = " ".join(rng.choice(words) for _ in range(rng.randint(1, 30)))
        result = si.analyse(rows, sql, schema)
        assert all(isinstance(i, ScanIssue) for i in result)
    conn.close()


# ---------------------------------------------------------------- check()


class _Engine:
    pass


class _Pooled:
    def __init__(self, raw: object) -> None:
        self.dbapi_connection = raw


class _Conn:
    def __init__(self, raw: object, engine: object) -> None:
        self.connection = _Pooled(raw)
        self.engine = engine


def test_check_records_an_explain_error_once(db: sqlite3.Connection) -> None:
    from pytest_queryguard.collector import QueryCollector

    target = QueryCollector(index_check=True)
    conn = _Conn(db, _Engine())
    bad = "SELECT * FROM ghost WHERE x = ?"
    si.check(conn, bad, (1,), False, [target])
    si.check(conn, bad, (1,), False, [target])  # the same shape: not retried
    assert [m for m, _ in target.explain_errors] == ["no such table: ghost"]
    assert target.scans == []


def test_check_skips_executemany_and_other_verbs(db: sqlite3.Connection) -> None:
    from pytest_queryguard.collector import QueryCollector

    target = QueryCollector(index_check=True)
    conn = _Conn(db, _Engine())
    si.check(
        conn, "UPDATE orders SET total = 1 WHERE status = ?", [("x",)], True, [target]
    )
    si.check(conn, "INSERT INTO tags VALUES (?, ?)", (1, "x"), False, [target])
    si.check(conn, "PRAGMA table_info(orders)", (), False, [target])
    assert target.scans == [] and target.explain_errors == [] and not target.explained


def test_check_records_a_scan_event_with_the_callsite(db: sqlite3.Connection) -> None:
    from pytest_queryguard.collector import QueryCollector

    first, second = QueryCollector(index_check=True), QueryCollector(index_check=True)
    conn = _Conn(db, _Engine())
    sql = "SELECT * FROM orders WHERE status = ?"
    si.check(conn, sql, ("x",), False, [first, second])
    assert [s.issue.table for s in first.scans] == ["orders"]
    assert first.scans == second.scans
    assert first.scans[0].sql == sql
    # a collector that has seen the shape does not explain it again
    third = QueryCollector(index_check=True)
    si.check(conn, sql, ("y",), False, [first, third])
    assert len(first.scans) == 1 and len(third.scans) == 1


def test_a_failing_schema_lookup_is_an_explain_error() -> None:
    from pytest_queryguard.collector import QueryCollector

    class Raw:
        def cursor(self) -> object:
            return self

        def execute(self, sql: str, params: object = ()) -> None:
            if "_master" in sql:
                raise sqlite3.OperationalError("database disk image is malformed")

        def fetchall(self) -> list[tuple[int, int, int, str]]:
            return [(2, 0, 0, "SCAN orders")]

        def close(self) -> None:
            pass

    target = QueryCollector(index_check=True)
    si.check(
        _Conn(Raw(), _Engine()),
        "SELECT * FROM orders WHERE a = ?",
        (1,),
        False,
        [target],
    )
    assert target.explain_errors == [
        ("database disk image is malformed", "SELECT * FROM orders WHERE a = ?")
    ]


def test_automatic_index_of_a_left_join(db: sqlite3.Connection) -> None:
    # Newer SQLite ends the detail with LEFT-JOIN.
    sql = "SELECT * FROM tags t LEFT JOIN orders o ON o.customer_id = t.order_id"
    schema = si.Schema(db.cursor())
    rows = [
        (3, 0, 0, "SCAN t"),
        (9, 0, 0, "SEARCH o USING AUTOMATIC COVERING INDEX (customer_id=?) LEFT-JOIN"),
    ]
    found = si.analyse(rows, sql, schema)
    assert [(i.table, i.column, i.condition) for i in found] == [
        ("orders", "customer_id", "customer_id=?")
    ]
    plan = [r[3] for r in db.execute("EXPLAIN QUERY PLAN " + sql)]
    if any("AUTOMATIC" in d for d in plan):  # the real plan, whatever its text
        assert [i.table for i in issues(db, sql)] == ["orders"]


def test_advice_has_the_create_index_and_the_alembic_call(
    db: sqlite3.Connection,
) -> None:
    issue = one(db, "SELECT * FROM orders WHERE customer_id = ? AND status = ?", 1, "x")
    assert issue.advice is not None
    assert [k.column for k in issue.advice.keys] == ["customer_id", "status"]
    assert issue.advice.create_sql == (
        "CREATE INDEX ix_orders_customer_id_status ON orders (customer_id, status)"
    )
    assert issue.advice.alembic == (
        'op.create_index("ix_orders_customer_id_status", "orders", '
        '["customer_id", "status"])'
    )
    assert issue.advice.verification is None


def test_advice_without_the_advisor_has_no_alembic_call(
    db: sqlite3.Connection,
) -> None:
    cursor = db.cursor()
    sql = "SELECT * FROM orders WHERE customer_id = ?"
    cursor.execute("EXPLAIN QUERY PLAN " + sql, (1,))
    rows = [si._row(r) for r in cursor.fetchall()]
    (issue,) = si.analyse(rows, sql, si.Schema(cursor), alembic=False)
    assert issue.advice is not None and issue.advice.alembic is None
    assert fix(issue) == "CREATE INDEX ix_orders_customer_id ON orders (customer_id)"
