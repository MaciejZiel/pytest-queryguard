from __future__ import annotations

import json
from typing import Any

import pytest

from pytest_queryguard.collector import QueryCollector
from pytest_queryguard.events import ScanEvent, ScanIssue
from pytest_queryguard.indexes import (
    ExplainError,
    check,
    find_scans,
    hint_column,
    is_checked_statement,
    quote_ident,
    run_explain,
    select_findings,
    table_label,
    to_numbered,
    unsupported_reason,
)

# ---------------------------------------------------------------- to_numbered


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (
            "SELECT a FROM t WHERE a = %s AND b = %s",
            "SELECT a FROM t WHERE a = $1 AND b = $2",
        ),
        (
            "SELECT a FROM t WHERE a = %s::INTEGER",
            "SELECT a FROM t WHERE a = $1::INTEGER",
        ),
        ("SELECT a %% 2 FROM t WHERE a = %s", "SELECT a % 2 FROM t WHERE a = $1"),
        (
            "SELECT a FROM t WHERE a IN (%s, %s, %s)",
            "SELECT a FROM t WHERE a IN ($1, $2, $3)",
        ),
        # quoted text is copied as it is
        (
            "SELECT '%s', \"%s\" FROM t WHERE a = %s",
            "SELECT '%s', \"%s\" FROM t WHERE a = $1",
        ),
        (
            "SELECT 'it''s %s' FROM t WHERE a = %s",
            "SELECT 'it''s %s' FROM t WHERE a = $1",
        ),
        (
            "SELECT E'\\' %s' FROM t WHERE a = %s",
            "SELECT E'\\' %s' FROM t WHERE a = $1",
        ),
        ("SELECT $$ %s $$ FROM t WHERE a = %s", "SELECT $$ %s $$ FROM t WHERE a = $1"),
        (
            "SELECT $q$ %s $q$ FROM t WHERE a = %s",
            "SELECT $q$ %s $q$ FROM t WHERE a = $1",
        ),
        ("SELECT 1 -- %s\nFROM t WHERE a = %s", "SELECT 1 -- %s\nFROM t WHERE a = $1"),
        (
            "SELECT /* %s */ 1 FROM t WHERE a = %s",
            "SELECT /* %s */ 1 FROM t WHERE a = $1",
        ),
        # a lone % that is not a placeholder is left alone
        ("SELECT a % 2 FROM t", "SELECT a % 2 FROM t"),
    ],
)
def test_to_numbered_format(sql: str, expected: str) -> None:
    assert to_numbered(sql, "format") == expected


def test_to_numbered_pyformat_reuses_numbers_for_repeated_names() -> None:
    sql = "SELECT a FROM t WHERE a = %(x)s OR b = %(y)s OR c = %(x)s AND d LIKE 'a%%'"
    assert to_numbered(sql, "pyformat") == (
        "SELECT a FROM t WHERE a = $1 OR b = $2 OR c = $1 AND d LIKE 'a%%'"
    )


def test_to_numbered_pyformat_accepts_positional_and_escapes() -> None:
    assert to_numbered("SELECT a %% 2 FROM t WHERE a = %s", "pyformat") == (
        "SELECT a % 2 FROM t WHERE a = $1"
    )


def test_to_numbered_numeric_dollar_is_unchanged() -> None:
    sql = "SELECT a FROM t WHERE a = $1::INTEGER"
    assert to_numbered(sql, "numeric_dollar") == sql


@pytest.mark.parametrize("style", ["qmark", "named", "numeric"])
def test_to_numbered_other_styles_are_unsupported(style: str) -> None:
    assert to_numbered("SELECT a FROM t WHERE a = ?", style) is None


def test_to_numbered_unterminated_quote_is_copied() -> None:
    assert to_numbered("SELECT 'abc %s", "format") == "SELECT 'abc %s"


# ------------------------------------------------------- is_checked_statement


@pytest.mark.parametrize(
    ("sql", "checked"),
    [
        ("SELECT 1", True),
        ("  select a from t", True),
        ("\nWITH x AS (SELECT 1) SELECT * FROM x", True),
        ("(SELECT a FROM t) UNION (SELECT a FROM u)", True),
        ("INSERT INTO t VALUES (1)", False),
        ("UPDATE t SET a = 1", False),
        ("DELETE FROM t", False),
        ("SHOW search_path", False),
        ("SELECTED", False),
        ("", False),
    ],
)
def test_is_checked_statement(sql: str, checked: bool) -> None:
    assert is_checked_statement(sql) is checked


# ------------------------------------------------------------------ find_scans


def _seq(alias: str = "orders", **extra: Any) -> dict[str, Any]:
    node: dict[str, Any] = {
        "Node Type": "Seq Scan",
        "Relation Name": "orders",
        "Schema": "public",
        "Alias": alias,
    }
    node.update(extra)
    return node


def test_seq_scan_with_filter_is_reported() -> None:
    [issue] = find_scans(_seq(Filter="(orders.customer_id = $1)"))
    assert issue == ScanIssue(
        schema="public",
        table="orders",
        node="Seq Scan",
        kind="filter",
        condition="(orders.customer_id = $1)",
        column="customer_id",
    )


def test_seq_scan_without_filter_or_join_is_not_reported() -> None:
    assert find_scans(_seq()) == []
    assert find_scans({"Node Type": "Aggregate", "Plans": [_seq()]}) == []


def test_index_scan_without_index_cond_but_with_filter_is_reported() -> None:
    plan = {
        "Node Type": "Limit",
        "Plans": [
            {
                "Node Type": "Index Scan",
                "Index Name": "orders_pkey",
                "Relation Name": "orders",
                "Schema": "public",
                "Alias": "orders",
                "Filter": "(orders.customer_id = $1)",
            }
        ],
    }
    [issue] = find_scans(plan)
    assert issue.node == "Index Scan using orders_pkey"
    assert issue.kind == "filter"
    assert issue.column == "customer_id"


@pytest.mark.parametrize("node_type", ["Index Scan", "Index Only Scan"])
def test_index_scan_with_index_cond_is_not_reported(node_type: str) -> None:
    node = {
        "Node Type": node_type,
        "Index Name": "orders_pkey",
        "Relation Name": "orders",
        "Schema": "public",
        "Alias": "orders",
        "Index Cond": "(orders.id = $1)",
        "Filter": "(orders.status = $2)",
    }
    assert find_scans(node) == []


def test_index_scan_without_filter_is_not_reported() -> None:
    node = {
        "Node Type": "Index Scan",
        "Index Name": "orders_pkey",
        "Relation Name": "orders",
        "Schema": "public",
        "Alias": "orders",
    }
    assert find_scans(node) == []


def test_bitmap_heap_scan_is_not_reported() -> None:
    node = {
        "Node Type": "Bitmap Heap Scan",
        "Relation Name": "orders",
        "Schema": "public",
        "Alias": "orders",
        "Recheck Cond": "(orders.status = $1)",
        "Filter": "(orders.total > $2)",
    }
    assert find_scans(node) == []


def test_seq_scan_below_a_join_on_its_column_is_reported() -> None:
    plan = {
        "Node Type": "Hash Join",
        "Hash Cond": "(o.customer_id = c.id)",
        "Plans": [
            _seq("o"),
            {
                "Node Type": "Hash",
                "Plans": [
                    {
                        "Node Type": "Index Scan",
                        "Index Name": "customers_pkey",
                        "Relation Name": "customers",
                        "Schema": "public",
                        "Alias": "c",
                    }
                ],
            },
        ],
    }
    [issue] = find_scans(plan)
    assert (issue.table, issue.kind, issue.condition, issue.column) == (
        "orders",
        "join",
        "(o.customer_id = c.id)",
        "customer_id",
    )


@pytest.mark.parametrize("key", ["Hash Cond", "Merge Cond", "Join Filter"])
def test_join_conditions_reach_scans_through_intermediate_nodes(key: str) -> None:
    plan = {
        "Node Type": "Nested Loop",
        key: "(c.id = o.customer_id)",
        "Plans": [
            {
                "Node Type": "Hash",
                "Plans": [{"Node Type": "Aggregate", "Plans": [_seq("o")]}],
            }
        ],
    }
    [issue] = find_scans(plan)
    assert issue.kind == "join"
    assert issue.column == "customer_id"


def test_join_condition_on_other_tables_is_not_reported() -> None:
    plan = {
        "Node Type": "Hash Join",
        "Hash Cond": "(a.x = b.y)",
        "Plans": [_seq("o")],
    }
    assert find_scans(plan) == []


def test_nearest_join_that_uses_the_table_wins() -> None:
    plan = {
        "Node Type": "Hash Join",
        "Hash Cond": "(o.customer_id = c.id)",
        "Plans": [
            {
                "Node Type": "Hash Join",
                "Hash Cond": "(o.status = s.code)",
                "Plans": [_seq("o")],
            }
        ],
    }
    [issue] = find_scans(plan)
    assert issue.condition == "(o.status = s.code)"


def test_join_conditions_do_not_reach_subplans() -> None:
    plan = {
        "Node Type": "Hash Join",
        "Hash Cond": "(o.customer_id = c.id)",
        "Plans": [
            {
                "Node Type": "Result",
                "Plans": [{**_seq("o"), "Parent Relationship": "SubPlan"}],
            }
        ],
    }
    assert find_scans(plan) == []


@pytest.mark.parametrize("schema", ["pg_catalog", "information_schema", "pg_toast"])
def test_system_schemas_are_not_reported(schema: str) -> None:
    node = {
        "Node Type": "Seq Scan",
        "Relation Name": "pg_class",
        "Schema": schema,
        "Alias": "pg_class",
        "Filter": "(pg_class.relname = $1)",
    }
    assert find_scans(node) == []


def test_scans_are_reported_in_plan_order() -> None:
    plan = {
        "Node Type": "Nested Loop",
        "Plans": [
            _seq("o", Filter="(o.status = $1)"),
            {
                "Node Type": "Seq Scan",
                "Relation Name": "customers",
                "Schema": "public",
                "Alias": "c",
                "Filter": "((c.name)::text = $2)",
            },
        ],
    }
    assert [(i.table, i.column) for i in find_scans(plan)] == [
        ("orders", "status"),
        ("customers", "name"),
    ]


def test_find_scans_ignores_malformed_nodes() -> None:
    plan: dict[str, Any] = {
        "Node Type": "Nested Loop",
        "Hash Cond": 5,
        "Plans": [None, "x", {"Node Type": "Seq Scan", "Filter": "(a = 1)"}],
    }
    assert find_scans(plan) == []


def test_find_scans_on_real_postgres_json() -> None:
    # EXPLAIN (GENERIC_PLAN, VERBOSE, FORMAT JSON) output from PostgreSQL 17
    # for a joinedload with no index on orders.customer_id.
    raw = """
    {"Node Type": "Nested Loop", "Join Type": "Left", "Plans": [
      {"Node Type": "Index Only Scan", "Parent Relationship": "Outer",
       "Index Name": "customers_pkey", "Relation Name": "customers",
       "Schema": "public", "Alias": "customers",
       "Index Cond": "(customers.id = $1)"},
      {"Node Type": "Seq Scan", "Parent Relationship": "Inner",
       "Relation Name": "orders", "Schema": "public", "Alias": "orders_1",
       "Filter": "(orders_1.customer_id = $1)"}]}
    """
    [issue] = find_scans(json.loads(raw))
    assert (issue.table, issue.condition, issue.column) == (
        "orders",
        "(orders_1.customer_id = $1)",
        "customer_id",
    )


# ----------------------------------------------------------------- hint_column


@pytest.mark.parametrize(
    ("condition", "alias", "column"),
    [
        ("(orders.customer_id = $1)", "orders", "customer_id"),
        ("(orders.customer_id = ANY (ARRAY[$1, $2]))", "orders", "customer_id"),
        ("((orders.status)::text = $1)", "orders", "status"),
        ("((o.created)::timestamp without time zone >= $1)", "o", "created"),
        ("(o.total < $1)", "o", "total"),
        ("(o.total <= $1)", "o", "total"),
        ("(o.total > $1)", "o", "total"),
        ("(o.total >= $1)", "o", "total"),
        ("(c.id = o.customer_id)", "o", "customer_id"),
        ("((o.total > $1) AND (o.total < $2))", "o", "total"),
        ('("Order".customer_id = $1)', "Order", "customer_id"),
        ('(o."customerId" = $1)', "o", '"customerId"'),
        ("((o.name)::text = 'o.other'::text)", "o", "name"),
    ],
)
def test_hint_column_finds_the_obvious_column(
    condition: str, alias: str, column: str
) -> None:
    assert hint_column(condition, alias) == column


@pytest.mark.parametrize(
    ("condition", "alias"),
    [
        # two columns of the scanned table
        ("((o.a = $1) AND (o.b = $2))", "o"),
        # a function around the column needs an expression index
        ("(lower((o.name)::text) = $1)", "o"),
        # LIKE, <> and IS NULL are not served by a plain btree index here
        ("((o.name)::text ~~ $1)", "o"),
        ("(o.status <> $1)", "o"),
        ("(o.status IS NULL)", "o"),
        # array membership needs a GIN index
        ("($1 = ANY (o.tags))", "o"),
        # JSON operators
        ("((o.data ->> 'k'::text) = $1)", "o"),
        ("($1 = (o.data -> 'k'::text))", "o"),
        # no column of the scanned table
        ("($1 IS NULL)", "o"),
        ("(c.id = $1)", "o"),
        # alias only as part of another name
        ("(xo.id = $1)", "o"),
    ],
)
def test_hint_column_gives_up_when_not_obvious(condition: str, alias: str) -> None:
    assert hint_column(condition, alias) is None


# ------------------------------------------------------- quoting and labels


@pytest.mark.parametrize(
    ("name", "quoted"),
    [
        ("orders", "orders"),
        ("order_items2", "order_items2"),
        ("Orders", '"Orders"'),
        ("order", '"order"'),
        ("user", '"user"'),
        ("my table", '"my table"'),
        ('a"b', '"a""b"'),
        ("1abc", '"1abc"'),
    ],
)
def test_quote_ident(name: str, quoted: str) -> None:
    assert quote_ident(name) == quoted


def test_table_label_hides_public_only() -> None:
    assert table_label("public", "orders") == "orders"
    assert table_label(None, "orders") == "orders"
    assert table_label("sales", "Orders") == 'sales."Orders"'


# ---------------------------------------------------------------- run_explain

PLAN = [{"Plan": {"Node Type": "Seq Scan"}}]


class FakeCursor:
    def __init__(self, conn: FakeConnection) -> None:
        self.conn = conn
        self.closed = False

    def execute(self, sql: str) -> None:
        self.conn.log.append(sql)
        for prefix, exc in self.conn.fail.items():
            if sql.startswith(prefix):
                raise exc

    def fetchone(self) -> tuple[Any]:
        return (self.conn.result,)

    def close(self) -> None:
        self.closed = True
        self.conn.closed_cursors += 1


class FakeConnection:
    def __init__(self, *, autocommit: bool = False, result: Any = PLAN) -> None:
        self.autocommit = autocommit
        self.result = result
        self.log: list[str] = []
        self.fail: dict[str, Exception] = {}
        self.closed_cursors = 0

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)


def _explain_sql(log: list[str]) -> str:
    [sql] = [s for s in log if s.startswith("EXPLAIN")]
    return sql


def test_run_explain_uses_a_savepoint_inside_a_transaction() -> None:
    conn = FakeConnection()
    assert run_explain(conn, "SELECT 1") == {"Node Type": "Seq Scan"}
    assert conn.log[0] == "SAVEPOINT queryguard_explain"
    assert "set_config('enable_seqscan', 'off', true)" in conn.log[1]
    assert _explain_sql(conn.log) == (
        "EXPLAIN (GENERIC_PLAN, VERBOSE, FORMAT JSON) SELECT 1"
    )
    assert conn.log[-2:] == [
        "ROLLBACK TO SAVEPOINT queryguard_explain",
        "RELEASE SAVEPOINT queryguard_explain",
    ]
    assert conn.closed_cursors == 1


def test_run_explain_uses_its_own_transaction_in_autocommit() -> None:
    conn = FakeConnection(autocommit=True)
    assert run_explain(conn, "SELECT 1") == {"Node Type": "Seq Scan"}
    assert conn.log[0] == "BEGIN"
    assert conn.log[-1] == "ROLLBACK"


def test_run_explain_accepts_json_as_text() -> None:
    conn = FakeConnection(result=json.dumps(PLAN))
    assert run_explain(conn, "SELECT 1") == {"Node Type": "Seq Scan"}


def test_run_explain_rolls_back_when_explain_fails() -> None:
    conn = FakeConnection()
    conn.fail["EXPLAIN"] = RuntimeError("could not determine data type of parameter $1")
    outcome = run_explain(conn, "SELECT $1")
    assert outcome == ExplainError("could not determine data type of parameter $1")
    assert conn.log[-2:] == [
        "ROLLBACK TO SAVEPOINT queryguard_explain",
        "RELEASE SAVEPOINT queryguard_explain",
    ]
    assert conn.closed_cursors == 1


def test_run_explain_reads_the_message_of_a_pg8000_error() -> None:
    conn = FakeConnection()
    conn.fail["EXPLAIN"] = RuntimeError(
        {"S": "ERROR", "C": "42P18", "M": "could not determine data type"}
    )
    assert run_explain(conn, "SELECT $1") == ExplainError(
        "could not determine data type"
    )


def test_run_explain_keeps_only_the_first_line_of_an_error() -> None:
    conn = FakeConnection()
    conn.fail["EXPLAIN"] = RuntimeError("syntax error\nLINE 1: EXPLAIN ...\n   ^")
    assert run_explain(conn, "SELECT") == ExplainError("syntax error")


@pytest.mark.parametrize("result", [None, [], [{}], "not json", [{"Plan": 3}]])
def test_run_explain_reports_an_unreadable_plan(result: Any) -> None:
    conn = FakeConnection(result=result)
    outcome = run_explain(conn, "SELECT 1")
    assert isinstance(outcome, ExplainError)
    assert outcome.message.startswith("unexpected EXPLAIN output")


@pytest.mark.parametrize(
    "step", ["SAVEPOINT", "ROLLBACK TO SAVEPOINT", "RELEASE SAVEPOINT"]
)
def test_run_explain_raises_when_the_savepoint_cannot_be_managed(step: str) -> None:
    conn = FakeConnection()
    conn.fail[step] = RuntimeError("connection lost")
    with pytest.raises(RuntimeError, match="connection lost"):
        run_explain(conn, "SELECT 1")
    assert conn.closed_cursors == 1


# ---------------------------------------------------------- unsupported_reason


class FakeDialect:
    def __init__(
        self,
        name: str = "postgresql",
        *,
        driver: str = "pg8000",
        is_async: bool = False,
        version: tuple[int, ...] | None = (17, 6),
        paramstyle: str = "format",
    ) -> None:
        self.name = name
        self.driver = driver
        self.is_async = is_async
        self.server_version_info = version
        self.paramstyle = paramstyle


def test_supported_dialect_has_no_reason() -> None:
    assert unsupported_reason(FakeDialect()) is None
    assert unsupported_reason(FakeDialect(version=(16, 0))) is None
    # an unknown version is tried; a failing EXPLAIN is reported separately
    assert unsupported_reason(FakeDialect(version=None)) is None


@pytest.mark.parametrize(
    ("dialect", "reason"),
    [
        (
            FakeDialect("sqlite"),
            (
                "sqlite statements were not checked: "
                "the index check needs PostgreSQL 16 or newer"
            ),
        ),
        (
            FakeDialect(driver="asyncpg", is_async=True),
            (
                "statements from the async driver asyncpg were not checked: "
                "the index check supports sync engines only"
            ),
        ),
        (
            FakeDialect(version=(15, 8)),
            (
                "PostgreSQL 15.8 statements were not checked: "
                "the index check needs PostgreSQL 16 or newer"
            ),
        ),
        (
            FakeDialect(driver="odd", paramstyle="qmark"),
            (
                "statements from odd were not checked: "
                "its paramstyle 'qmark' is not supported by the index check"
            ),
        ),
    ],
)
def test_unsupported_reason(dialect: FakeDialect, reason: str) -> None:
    assert unsupported_reason(dialect) == reason


# ----------------------------------------------------------------------- check


class FakeProxy:
    def __init__(self, dbapi_connection: FakeConnection) -> None:
        self.dbapi_connection = dbapi_connection


class FakeSAConnection:
    """The parts of a SQLAlchemy Connection that check() uses."""

    def __init__(self, dialect: FakeDialect, result: Any = PLAN) -> None:
        self.dialect = dialect
        self.engine = object()
        self.raw = FakeConnection(result=result)
        self.connection = FakeProxy(self.raw)


SEQ_PLAN = [
    {
        "Plan": _seq(Filter="(orders.customer_id = $1)"),
    }
]


def _collector() -> QueryCollector:
    return QueryCollector(index_check=True)


def test_check_records_a_scan_with_the_statement_and_call_site() -> None:
    conn = FakeSAConnection(FakeDialect(), result=SEQ_PLAN)
    target = _collector()
    sql = "SELECT orders.id FROM orders WHERE orders.customer_id = %s::INTEGER"
    check(conn, sql, False, [target])
    assert _explain_sql(conn.raw.log).endswith("WHERE orders.customer_id = $1::INTEGER")
    [scan] = target.scans
    assert scan.sql == sql
    assert scan.issue.column == "customer_id"
    assert scan.callsite is not None
    assert (
        scan.callsite.function
        == "test_check_records_a_scan_with_the_statement_and_call_site"
    )


def test_check_explains_each_normalized_statement_once() -> None:
    conn = FakeSAConnection(FakeDialect(), result=SEQ_PLAN)
    target = _collector()
    check(conn, "SELECT a FROM t WHERE a IN (%s, %s)", False, [target])
    check(conn, "SELECT a FROM t WHERE a IN (%s, %s, %s)", False, [target])
    assert len([s for s in conn.raw.log if s.startswith("EXPLAIN")]) == 1
    assert len(target.scans) == 1


def test_check_explains_again_for_a_new_collector() -> None:
    conn = FakeSAConnection(FakeDialect(), result=SEQ_PLAN)
    first, second = _collector(), _collector()
    check(conn, "SELECT a FROM t WHERE a = %s", False, [first])
    check(conn, "SELECT a FROM t WHERE a = %s", False, [first, second])
    assert len([s for s in conn.raw.log if s.startswith("EXPLAIN")]) == 2
    assert (len(first.scans), len(second.scans)) == (1, 1)


@pytest.mark.parametrize(
    ("sql", "executemany"),
    [("INSERT INTO t VALUES (%s)", False), ("SELECT a FROM t WHERE a = %s", True)],
)
def test_check_skips_writes_and_executemany(sql: str, executemany: bool) -> None:
    conn = FakeSAConnection(FakeDialect())
    target = _collector()
    check(conn, sql, executemany, [target])
    assert conn.raw.log == []
    assert target.explained == set()


def test_check_records_a_notice_for_an_unsupported_engine() -> None:
    conn = FakeSAConnection(FakeDialect("sqlite"))
    target = _collector()
    check(conn, "SELECT 1", False, [target])
    check(conn, "SELECT 2", False, [target])
    assert conn.raw.log == []
    assert list(target.index_notices) == [
        (
            "sqlite statements were not checked: "
            "the index check needs PostgreSQL 16 or newer"
        )
    ]


def test_check_records_explain_errors() -> None:
    conn = FakeSAConnection(FakeDialect())
    conn.raw.fail["EXPLAIN"] = RuntimeError("cannot plan")
    target = _collector()
    check(conn, "SELECT $1", False, [target])
    assert target.explain_errors == [("cannot plan", "SELECT $1")]
    assert target.scans == []


def test_check_records_nothing_for_a_clean_plan() -> None:
    conn = FakeSAConnection(FakeDialect())
    target = _collector()
    check(conn, "SELECT 1", False, [target])
    assert (target.scans, target.explain_errors) == ([], [])


# ------------------------------------------------------------ select_findings


def _event(table: str, condition: str, schema: str = "public") -> ScanEvent:
    issue = ScanIssue(schema, table, "Seq Scan", "filter", condition, None)
    return ScanEvent(issue, f"SELECT * FROM {table}", None)


def test_select_findings_dedupes_by_table_and_condition() -> None:
    a1 = _event("orders", "(orders.a = $1)")
    a2 = _event("orders", "(orders.a = $1)")
    b = _event("orders", "(orders.b = $1)")
    assert select_findings([a1, a2, b], []) == [a1, b]


@pytest.mark.parametrize(
    "pattern", ["countries", "public.countries", "count*", "public.*"]
)
def test_select_findings_drops_ignored_tables(pattern: str) -> None:
    kept = _event("orders", "(orders.a = $1)", schema="sales")
    dropped = _event("countries", "(countries.code = $1)")
    assert select_findings([kept, dropped], [pattern]) == [kept]


def test_ignore_patterns_are_case_sensitive() -> None:
    event = _event("Countries", '("Countries".code = $1)')
    assert select_findings([event], ["countries"]) == [event]
