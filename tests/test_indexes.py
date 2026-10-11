from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import pytest

from pytest_queryguard import indexes
from pytest_queryguard.collector import QueryCollector
from pytest_queryguard.events import Catalog, ScanEvent, ScanIssue
from pytest_queryguard.indexes import (
    Explained,
    ExplainError,
    SavepointError,
    check,
    find_scans,
    hint_column,
    index_names,
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


@pytest.mark.parametrize("style", ["format", "pyformat"])
def test_to_numbered_unescapes_percent_in_quotes_for_libpq_drivers(style: str) -> None:
    # psycopg and psycopg2 replace %% everywhere, also inside quoted text;
    # pg8000 leaves quoted text alone.
    sql = "SELECT 'x%%', \"a%%b\" FROM t WHERE a LIKE 'y%%' AND b = %s AND c %% 2 = 0"
    assert to_numbered(sql, style, unescape_quoted=True) == (
        "SELECT 'x%', \"a%b\" FROM t WHERE a LIKE 'y%' AND b = $1 AND c % 2 = 0"
    )
    assert to_numbered(sql, style) == (
        "SELECT 'x%%', \"a%%b\" FROM t WHERE a LIKE 'y%%' AND b = $1 AND c % 2 = 0"
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
        # sqlcommenter-style tags and other leading comments
        ("-- c\nSELECT 1", True),
        ("/* app=x */ SELECT 1", True),
        ("/* a */ -- b\n /* c\n d */ (WITH x AS (SELECT 1) SELECT 1)", True),
        ("-- SELECT\nUPDATE t SET a = 1", False),
        ("/* SELECT */ DELETE FROM t", False),
        ("-- only a comment", False),
        ("/* unterminated SELECT", False),
    ],
)
def test_is_checked_statement(sql: str, checked: bool) -> None:
    assert is_checked_statement(sql) is checked


@pytest.mark.parametrize(
    "prefix",
    [
        "\n" * 200 + " " * 200,
        "  \n" * 300,
        "(" * 300,
        "/* tag */ " * 200,
        "-- c\n" * 200,
        "/* unterminated " + " " * 300,
    ],
)
@pytest.mark.parametrize(("verb", "checked"), [("UPDATE", False), ("SELECT", True)])
def test_is_checked_statement_is_linear_in_the_prefix(
    prefix: str, verb: str, checked: bool
) -> None:
    # A nested regex quantifier once took 20 s for 28 leading spaces.
    sql = prefix + verb + " t SET a = 1"
    expected = checked and not prefix.startswith("/* unterminated")
    start = time.perf_counter()
    for _ in range(10):
        assert is_checked_statement(sql) is expected
    assert time.perf_counter() - start < 0.05


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
        show_schema=False,
    )


def test_seq_scan_without_filter_is_not_reported() -> None:
    assert find_scans(_seq()) == []
    assert find_scans({"Node Type": "Aggregate", "Plans": [_seq()]}) == []


def test_seq_scan_below_a_join_without_filter_is_not_reported() -> None:
    # Whether a join reads the unindexed side in full depends on statistics,
    # so join conditions alone are never reported.
    plan = {
        "Node Type": "Hash Join",
        "Hash Cond": "(o.customer_id = c.id)",
        "Plans": [_seq("o"), {"Node Type": "Hash", "Plans": []}],
    }
    assert find_scans(plan) == []


def test_correlated_subplan_is_reported_through_its_filter() -> None:
    plan = {
        "Node Type": "Index Only Scan",
        "Index Name": "customers_pkey",
        "Relation Name": "customers",
        "Schema": "public",
        "Alias": "c",
        "Plans": [
            {
                "Node Type": "Aggregate",
                "Parent Relationship": "SubPlan",
                "Plans": [_seq("o", Filter="(o.customer_id = c.id)")],
            }
        ],
    }
    [issue] = find_scans(plan)
    assert (issue.table, issue.column) == ("orders", "customer_id")


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


def _pkey_scan(condition: str, node_type: str = "Index Scan") -> dict[str, Any]:
    return {
        "Node Type": "Limit",
        "Plans": [
            {
                "Node Type": node_type,
                "Index Name": "orders_pkey",
                "Relation Name": "orders",
                "Schema": "public",
                "Alias": "orders",
                "Filter": condition,
            }
        ],
    }


@pytest.mark.parametrize(
    "condition",
    [
        "(orders.customer_id = $1)",
        "(orders.customer_id = ANY ($1))",
        "(orders.customer_id = ANY (ARRAY[$1, $2]))",
        "((orders.status)::text = $1)",
        "((orders.customer_id = $1) OR (orders.customer_id = $2))",
    ],
)
@pytest.mark.parametrize("node_type", ["Index Scan", "Index Only Scan"])
def test_whole_index_scan_with_an_equality_filter_is_reported(
    condition: str, node_type: str
) -> None:
    [issue] = find_scans(_pkey_scan(condition, node_type), None, None, VARCHAR)
    assert issue.column in ("customer_id", "status")


# varchar columns, as run_explain() reads them from pg_attribute
VARCHAR = {("public", "orders"): frozenset({"status", "name"})}


@pytest.mark.parametrize(
    ("condition", "varchar", "column"),
    [
        ("((orders.status)::text = $1)", VARCHAR, "status"),
        # an int, an enum or char(n) printed with ::text: no plain index helps
        ("((orders.id)::text = $1)", VARCHAR, None),
        ("((orders.kind)::text = $1)", VARCHAR, None),
        # without type information the cast is not trusted
        ("((orders.status)::text = $1)", None, None),
    ],
)
def test_text_cast_hint_needs_a_varchar_column(
    condition: str, varchar: Any, column: str | None
) -> None:
    [issue] = find_scans(_seq(Filter=condition), None, None, varchar)
    assert issue.column == column
    assert issue.expression is (column is None)


def test_whole_index_scan_with_a_cast_on_a_non_varchar_column_is_not_reported() -> None:
    plan = _pkey_scan("((orders.id)::text = $1)", "Index Only Scan")
    assert find_scans(plan, None, None, VARCHAR) == []


def _bitmap_whole_index(
    condition: str, *, recheck: str | None = None, index_cond: str | None = None
) -> dict[str, Any]:
    index: dict[str, Any] = {"Node Type": "Bitmap Index Scan", "Index Name": "o_inc"}
    if index_cond:
        index["Index Cond"] = index_cond
    heap: dict[str, Any] = {
        "Node Type": "Bitmap Heap Scan",
        "Relation Name": "orders",
        "Schema": "public",
        "Alias": "orders",
        "Filter": condition,
        "Plans": [index],
    }
    if recheck:
        heap["Recheck Cond"] = recheck
    return heap


def test_bitmap_scan_of_a_whole_index_with_an_equality_filter_is_reported() -> None:
    # Before ANALYZE, an INCLUDE index can be read this way instead of an
    # Index Only Scan with the same filter.
    [issue] = find_scans(_bitmap_whole_index("(orders.note = $1)"))
    assert issue.node == "Bitmap Heap Scan using o_inc"
    assert (issue.kind, issue.column) == ("filter", "note")


@pytest.mark.parametrize(
    "plan",
    [
        _bitmap_whole_index("(orders.total > $1)"),
        _bitmap_whole_index("(orders.note = $1)", recheck="(orders.a = $2)"),
        _bitmap_whole_index("(orders.note = $1)", index_cond="(orders.a = $2)"),
    ],
)
def test_other_bitmap_scans_are_not_reported_by_rule_2(plan: dict[str, Any]) -> None:
    assert find_scans(plan) == []


@pytest.mark.parametrize(
    "condition",
    [
        # pagination: ORDER BY id LIMIT n walks the primary key on purpose
        "(orders.total > '0'::numeric)",
        "(orders.created_at IS NULL)",
        "(orders.total <= $1)",
        "((orders.total > $1) AND (orders.total < $2))",
        # equality on more than one column, or not on a plain column
        "((orders.customer_id = $1) AND (orders.status = $2))",
        "(lower(orders.status) = $1)",
        "((orders.total)::integer = $1)",
    ],
)
def test_whole_index_scan_without_an_equality_on_one_column_is_not_reported(
    condition: str,
) -> None:
    assert find_scans(_pkey_scan(condition)) == []


# Leading key columns, as run_explain() reads them from pg_index.
LEADING = {("public", "orders_tenant_id_status_idx"): "tenant_id"}


def _composite(node_type: str, condition: str) -> dict[str, Any]:
    if node_type == "Bitmap Index Scan":
        return {
            "Node Type": "Bitmap Heap Scan",
            "Relation Name": "orders",
            "Schema": "public",
            "Alias": "orders",
            "Recheck Cond": condition,
            "Plans": [
                {
                    "Node Type": "BitmapAnd",
                    "Plans": [
                        {
                            "Node Type": "Bitmap Index Scan",
                            "Index Name": "orders_tenant_id_status_idx",
                            "Index Cond": condition,
                        }
                    ],
                }
            ],
        }
    return {
        "Node Type": node_type,
        "Index Name": "orders_tenant_id_status_idx",
        "Relation Name": "orders",
        "Schema": "public",
        "Alias": "orders",
        "Index Cond": condition,
    }


@pytest.mark.parametrize(
    "node_type", ["Index Scan", "Index Only Scan", "Bitmap Index Scan"]
)
def test_index_cond_on_a_trailing_column_only_is_reported(node_type: str) -> None:
    [issue] = find_scans(_composite(node_type, "(orders.status = $1)"), None, LEADING)
    assert issue.node == f"{node_type} using orders_tenant_id_status_idx"
    assert issue.kind == "index condition"
    assert issue.condition == "(orders.status = $1)"
    assert issue.column == "status"


@pytest.mark.parametrize(
    "condition",
    [
        "(orders.tenant_id = $1)",
        "((orders.tenant_id = $1) AND (orders.status = $2))",
        # a range on the trailing column: same constraints as for filters
        "(orders.status > $1)",
    ],
)
@pytest.mark.parametrize("node_type", ["Index Scan", "Bitmap Index Scan"])
def test_index_cond_on_the_leading_column_is_not_reported(
    node_type: str, condition: str
) -> None:
    assert find_scans(_composite(node_type, condition), None, LEADING) == []


def test_index_cond_is_not_judged_without_the_leading_column() -> None:
    plan = _composite("Index Scan", "(orders.status = $1)")
    assert find_scans(plan) == []
    assert find_scans(plan, None, {("public", "other_idx"): "x"}) == []


def test_index_names_lists_indexes_used_with_a_condition() -> None:
    plan = {
        "Node Type": "Nested Loop",
        "Plans": [
            _composite("Bitmap Index Scan", "(orders.status = $1)"),
            {
                "Node Type": "Index Scan",
                "Index Name": "customers_pkey",
                "Relation Name": "customers",
                "Schema": "sales",
                "Alias": "c",
                "Index Cond": "(c.id = $1)",
            },
            {
                "Node Type": "Index Scan",
                "Index Name": "orders_pkey",
                "Relation Name": "orders",
                "Schema": "public",
                "Alias": "orders",
            },
        ],
    }
    assert index_names(plan) == {
        ("public", "orders_tenant_id_status_idx"),
        ("sales", "customers_pkey"),
    }


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
    varchar = {("public", "customers"): frozenset({"name"})}
    assert [(i.table, i.column) for i in find_scans(plan, None, None, varchar)] == [
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
    assert hint_column(condition, alias, frozenset({"status", "name"})) == column


@pytest.mark.parametrize(
    ("condition", "alias"),
    [
        # two columns of the scanned table
        ("((o.a = $1) AND (o.b = $2))", "o"),
        # a function around the column needs an expression index
        ("(lower((o.name)::text) = $1)", "o"),
        # so does a cast other than the varchar-to-text one the plan prints
        ("((o.total)::integer = $1)", "o"),
        ("((o.created_at)::date = $1)", "o"),
        ("((o.created)::timestamp without time zone >= $1)", "o"),
        ("($1 = (o.total)::integer)", "o"),
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


@pytest.mark.parametrize(
    ("condition", "expression"),
    [
        ("((o.total)::integer = $1)", True),
        ("(lower((o.name)::text) = $1)", True),
        ("((o.created_at)::date = $1)", True),
        ("((o.a = $1) AND (o.b = $2))", False),
        ("(o.name ~~ $1)", False),
        # without knowing that o.name is varchar, the cast may need one
        ("((o.name)::text ~~ $1)", True),
        ("(o.total = $1)", False),
        ("((o.id)::text = $1)", True),
    ],
)
def test_expression_conditions_are_flagged(condition: str, expression: bool) -> None:
    [issue] = find_scans(_seq("o", Filter=condition))
    assert issue.expression is expression


def test_hint_column_does_not_trust_text_casts_by_default() -> None:
    assert hint_column("((o.name)::text = $1)", "o") is None


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


def _label_of(schema: str | None, table: str, current: str | None) -> str:
    node = {
        "Node Type": "Seq Scan",
        "Relation Name": table,
        "Alias": table,
        "Filter": "(x = $1)",
    }
    if schema is not None:
        node["Schema"] = schema
    [issue] = find_scans(node, current)
    return table_label(issue)


def test_table_label_hides_the_current_schema() -> None:
    assert _label_of("public", "orders", None) == "orders"
    assert _label_of("sales", "orders", None) == "sales.orders"
    assert _label_of("sales", "orders", "sales") == "orders"
    assert _label_of("public", "orders", "sales") == "public.orders"
    assert _label_of(None, "orders", "sales") == "orders"
    assert _label_of("Sales", "Orders", None) == '"Sales"."Orders"'


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

    def fetchall(self) -> list[tuple[str, str, str]]:
        if "pg_index" in self.conn.log[-1]:
            return self.conn.leading_rows
        return self.conn.varchar_rows

    def fetchone(self) -> tuple[Any, ...]:
        if self.conn.log[-1].startswith("SELECT set_config"):
            return ("off", "1s", "5s", self.conn.current_schema)
        return (self.conn.result,)

    def close(self) -> None:
        self.closed = True
        self.conn.closed_cursors += 1


class FakeConnection:
    def __init__(self, *, autocommit: bool = False, result: Any = PLAN) -> None:
        self.autocommit = autocommit
        self.result = result
        self.current_schema: str | None = "public"
        self.leading_rows: list[tuple[str, str, str]] = []
        self.varchar_rows: list[tuple[str, str, str]] = []
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
    assert run_explain(conn, "SELECT 1") == Explained(
        {"Node Type": "Seq Scan"}, "public"
    )
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
    assert run_explain(conn, "SELECT 1") == Explained(
        {"Node Type": "Seq Scan"}, "public"
    )
    assert conn.log[0] == "BEGIN"
    assert conn.log[-1] == "ROLLBACK"


def test_run_explain_accepts_json_as_text() -> None:
    conn = FakeConnection(result=json.dumps(PLAN))
    assert run_explain(conn, "SELECT 1") == Explained(
        {"Node Type": "Seq Scan"}, "public"
    )


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
    with pytest.raises(SavepointError, match="connection lost"):
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


def _explains(conn: FakeSAConnection) -> int:
    return len([s for s in conn.raw.log if s.startswith("EXPLAIN")])


def test_check_explains_in_lists_of_any_length_once() -> None:
    conn = FakeSAConnection(FakeDialect(), result=SEQ_PLAN)
    target = _collector()
    check(conn, "SELECT a FROM t WHERE a IN (%s, %s)", False, [target])
    check(conn, "SELECT a FROM t\n WHERE a IN (%s, %s, %s)", False, [target])
    assert _explains(conn) == 1
    assert len(target.scans) == 1


def test_check_explains_statements_with_other_literals_again() -> None:
    # A partial index can serve status = 'open' but not status = 'closed'.
    conn = FakeSAConnection(FakeDialect(), result=SEQ_PLAN)
    target = _collector()
    check(conn, "SELECT a FROM t WHERE status = 'open'", False, [target])
    check(conn, "SELECT a FROM t WHERE status = 'closed'", False, [target])
    check(conn, "SELECT a FROM t WHERE status = 'open'", False, [target])
    assert _explains(conn) == 2


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


def test_select_findings_dedupes_one_column_under_several_aliases() -> None:
    def scan(condition: str) -> ScanEvent:
        issue = ScanIssue(
            "public", "orders", "Seq Scan", "filter", condition, "customer_id"
        )
        return ScanEvent(issue, "SELECT 1", None)

    first = scan("(orders.customer_id = $1)")
    union = scan("(orders_1.customer_id = $1)")
    assert select_findings([first, union], []) == [first]


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


def test_run_explain_reads_the_current_schema() -> None:
    conn = FakeConnection()
    conn.current_schema = "sales"
    assert run_explain(conn, "SELECT 1") == Explained(
        {"Node Type": "Seq Scan"}, "sales"
    )
    conn.current_schema = None
    assert run_explain(conn, "SELECT 1") == Explained({"Node Type": "Seq Scan"}, None)
    assert "current_schema()" in conn.log[1]


# ------------------------------------------------- leading key column lookup


def test_run_explain_looks_up_leading_columns_inside_the_savepoint() -> None:
    plan = [{"Plan": _composite("Index Scan", "(orders.status = $1)")}]
    conn = FakeConnection(result=plan)
    conn.leading_rows = [("public", "orders_tenant_id_status_idx", "tenant_id")]
    outcome = run_explain(conn, "SELECT 1", leading=True, catalog=Catalog())
    assert isinstance(outcome, Explained)
    assert outcome.leading == LEADING
    [lookup] = [sql for sql in conn.log if "pg_index" in sql]
    assert "('public', 'orders_tenant_id_status_idx')" in lookup
    assert conn.log.index(lookup) < conn.log.index(
        "ROLLBACK TO SAVEPOINT queryguard_explain"
    )


def test_run_explain_quotes_names_in_the_lookup() -> None:
    node = _composite("Index Scan", "(orders.status = $1)")
    node["Index Name"] = "it's"
    conn = FakeConnection(result=[{"Plan": node}])
    run_explain(conn, "SELECT 1", leading=True, catalog=Catalog())
    [lookup] = [sql for sql in conn.log if "pg_index" in sql]
    assert "('public', 'it''s')" in lookup


@pytest.mark.parametrize("name", ["back\\slash", "nul\x00"])
def test_run_explain_skips_names_it_cannot_quote(name: str) -> None:
    node = _composite("Index Scan", "(orders.status = $1)")
    node["Index Name"] = name
    conn = FakeConnection(result=[{"Plan": node}])
    outcome = run_explain(conn, "SELECT 1", leading=True, catalog=Catalog())
    assert isinstance(outcome, Explained)
    assert outcome.leading == {}
    assert not [sql for sql in conn.log if "pg_index" in sql]


def test_run_explain_without_index_conditions_skips_the_lookup() -> None:
    conn = FakeConnection(result=SEQ_PLAN)
    run_explain(conn, "SELECT 1", leading=True, catalog=Catalog())
    assert not [sql for sql in conn.log if "pg_index" in sql]


def test_run_explain_keeps_the_plan_when_the_lookup_fails() -> None:
    plan = [{"Plan": _composite("Index Scan", "((orders.status)::text = $1)")}]
    conn = FakeConnection(result=plan)
    conn.fail["SELECT n.nspname"] = RuntimeError("no pg_index today")
    outcome = run_explain(conn, "SELECT 1", leading=True, catalog=Catalog())
    assert isinstance(outcome, Explained)
    assert (outcome.leading, outcome.varchar) == ({}, {})
    # the aborted lookup is not followed by the varchar one
    assert len([sql for sql in conn.log if sql.startswith("SELECT n.nspname")]) == 1
    assert conn.log[-2] == "ROLLBACK TO SAVEPOINT queryguard_explain"


def test_leading_lookup_reads_btree_indexes_only() -> None:
    # Rule 3 is about btree: BRIN and GIN indexes on (a, b) serve b = $1.
    plan = [{"Plan": _composite("Index Scan", "(orders.status = $1)")}]
    conn = FakeConnection(result=plan)
    run_explain(conn, "SELECT 1", leading=True, catalog=Catalog())
    [lookup] = [sql for sql in conn.log if "pg_index" in sql]
    assert "JOIN pg_am am ON am.oid = c.relam" in lookup
    assert "am.amname = 'btree'" in lookup


def test_run_explain_looks_up_varchar_columns_of_text_casts() -> None:
    plan = [{"Plan": _seq(Filter="((orders.status)::text = $1)")}]
    conn = FakeConnection(result=plan)
    conn.varchar_rows = [("public", "orders", "status"), ("public", "orders", "name")]
    catalog = Catalog()
    outcome = run_explain(conn, "SELECT 1", catalog=catalog)
    assert isinstance(outcome, Explained)
    assert outcome.varchar == VARCHAR
    [lookup] = [sql for sql in conn.log if "pg_attribute" in sql and "typname" in sql]
    assert "('public', 'orders')" in lookup
    assert "t.typname = 'varchar'" in lookup
    # known tables are not looked up again
    run_explain(conn, "SELECT 1", catalog=catalog)
    assert len([sql for sql in conn.log if "typname" in sql]) == 1


def test_run_explain_skips_the_varchar_lookup_without_text_casts() -> None:
    conn = FakeConnection(result=SEQ_PLAN)
    run_explain(conn, "SELECT 1", catalog=Catalog())
    assert not [sql for sql in conn.log if "typname" in sql]


def test_run_explain_without_a_catalog_looks_nothing_up() -> None:
    plan = [{"Plan": _composite("Index Scan", "((orders.status)::text = $1)")}]
    conn = FakeConnection(result=plan)
    outcome = run_explain(conn, "SELECT 1", leading=True)
    assert isinstance(outcome, Explained)
    assert not [sql for sql in conn.log if sql.startswith("SELECT n.nspname")]


@pytest.mark.parametrize(
    ("rows", "column"), [([("public", "orders", "status")], "status"), ([], None)]
)
def test_check_gives_the_text_cast_hint_for_varchar_only(
    rows: list[tuple[str, str, str]], column: str | None
) -> None:
    plan = [{"Plan": _seq(Filter="((orders.status)::text = $1)")}]
    conn = FakeSAConnection(FakeDialect(), result=plan)
    conn.raw.varchar_rows = rows
    target = _collector()
    check(conn, "SELECT * FROM orders WHERE status = %s", False, [target])
    [scan] = target.scans
    assert scan.issue.column == column


@pytest.mark.parametrize(
    ("version", "lookups"), [((16, 4), 1), ((17, 6), 1), (None, 1), ((18, 0), 0)]
)
def test_check_looks_up_leading_columns_before_postgres_18(
    version: tuple[int, ...] | None, lookups: int
) -> None:
    # PostgreSQL 18 can skip-scan an index on a trailing column.
    plan = [{"Plan": _composite("Index Scan", "(orders.status = $1)")}]
    conn = FakeSAConnection(FakeDialect(version=version), result=plan)
    conn.raw.leading_rows = [("public", "orders_tenant_id_status_idx", "tenant_id")]
    target = _collector()
    check(conn, "SELECT * FROM orders WHERE status = %s", False, [target])
    assert len([sql for sql in conn.raw.log if "pg_index" in sql]) == lookups
    assert len(target.scans) == lookups


def test_check_looks_up_each_index_once_per_test() -> None:
    plan = [{"Plan": _composite("Index Scan", "(orders.status = $1)")}]
    conn = FakeSAConnection(FakeDialect(), result=plan)
    conn.raw.leading_rows = [("public", "orders_tenant_id_status_idx", "tenant_id")]
    target = _collector()
    check(conn, "SELECT * FROM orders WHERE status = %s", False, [target])
    check(conn, "SELECT id FROM orders WHERE status = %s", False, [target])
    assert len([sql for sql in conn.raw.log if "pg_index" in sql]) == 1
    assert len(target.scans) == 2
    # an index without a plain first column is remembered too
    conn.raw.leading_rows = []
    other = _collector()
    check(conn, "SELECT * FROM orders WHERE status = %s", False, [other])
    check(conn, "SELECT id FROM orders WHERE status = %s", False, [other])
    assert len([sql for sql in conn.raw.log if "pg_index" in sql]) == 2
    assert other.scans == []


def test_run_explain_skips_the_lookup_for_known_indexes() -> None:
    plan = [{"Plan": _composite("Index Scan", "(orders.status = $1)")}]
    conn = FakeConnection(result=plan)
    catalog = Catalog(leading={("public", "orders_tenant_id_status_idx"): "tenant_id"})
    outcome = run_explain(conn, "SELECT 1", leading=True, catalog=catalog)
    assert isinstance(outcome, Explained)
    assert outcome.leading == LEADING
    assert not [sql for sql in conn.log if "pg_index" in sql]


# ------------------------------------------------- quoted %% per driver


@pytest.mark.parametrize(
    ("driver", "explained"),
    [
        ("pg8000", "SELECT a FROM t WHERE a LIKE 'x%%' AND b = $1"),
        ("psycopg2", "SELECT a FROM t WHERE a LIKE 'x%' AND b = $1"),
        ("psycopg", "SELECT a FROM t WHERE a LIKE 'x%' AND b = $1"),
    ],
)
def test_check_unescapes_quoted_percent_except_for_pg8000(
    driver: str, explained: str
) -> None:
    conn = FakeSAConnection(FakeDialect(driver=driver, paramstyle="pyformat"))
    check(
        conn, "SELECT a FROM t WHERE a LIKE 'x%%' AND b = %(b)s", False, [_collector()]
    )
    assert _explain_sql(conn.raw.log).endswith(explained)


# ------------------------------------------- a broken savepoint stops the check


@pytest.fixture(autouse=True)
def fresh_index_state() -> Iterator[None]:
    old = indexes.swap_stopped(None)
    yield
    indexes.swap_stopped(old)


@pytest.mark.parametrize("step", ["SAVEPOINT", "ROLLBACK TO SAVEPOINT"])
def test_broken_savepoint_stops_only_the_index_check(
    fresh_index_state: None, step: str
) -> None:
    conn = FakeSAConnection(FakeDialect(), result=SEQ_PLAN)
    conn.raw.fail[step] = RuntimeError("connection lost")
    first = _collector()
    check(conn, "SELECT a FROM t WHERE a = %s", False, [first])
    notice = (
        "the index check stopped for the rest of the session: "
        "it could not open or roll back its savepoint: connection lost"
    )
    assert list(first.index_notices) == [notice]
    # later statements are not explained, and say why once more
    other = FakeSAConnection(FakeDialect(), result=SEQ_PLAN)
    second = _collector()
    check(other, "SELECT b FROM t WHERE b = %s", False, [second])
    assert other.raw.log == []
    assert list(second.index_notices) == [notice]


def test_broken_savepoint_keeps_the_rest_of_the_plugin_running(
    fresh_index_state: None,
) -> None:
    from pytest_queryguard import collector as collector_module
    from pytest_queryguard import listeners

    conn = FakeSAConnection(FakeDialect(), result=SEQ_PLAN)
    conn.raw.fail["ROLLBACK TO SAVEPOINT"] = RuntimeError("connection lost")
    target = _collector()
    collector_module.push(target)
    try:
        listeners._on_after_cursor_execute(
            conn, None, "SELECT a FROM t WHERE a = %s", (1,), None, False
        )
    finally:
        collector_module.pop(target)
    assert listeners.internal_error() is None
    assert target.index_notices


def test_swap_stopped_returns_the_previous_state(fresh_index_state: None) -> None:
    assert indexes.swap_stopped("x") is None
    assert indexes.swap_stopped(None) == "x"
