from __future__ import annotations

import gc
import json
import time
from collections.abc import Iterator
from typing import Any

import pytest

from pytest_queryguard import catalog as catalogs
from pytest_queryguard import indexes
from pytest_queryguard.collector import QueryCollector
from pytest_queryguard.events import Catalog, ScanEvent, ScanIssue
from pytest_queryguard.indexes import (
    Explained,
    ExplainError,
    SavepointError,
    check,
    explain_block,
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
    version_notice,
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
    # The regression this guards took 20 s; a loaded runner is far from 1 s.
    assert time.perf_counter() - start < 1.0


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


def test_bitmap_scan_of_a_whole_partial_index_is_reported() -> None:
    # Before ANALYZE, a partial index ... WHERE deleted_at IS NULL is read in
    # full as a bitmap; the Recheck Cond is then only the index predicate.
    plan = _bitmap_whole_index(
        "(orders.email = $1)", recheck="(orders.deleted_at IS NULL)"
    )
    [issue] = find_scans(plan)
    assert issue.node == "Bitmap Heap Scan using o_inc"
    assert (issue.kind, issue.condition, issue.column) == (
        "filter",
        "(orders.email = $1)",
        "email",
    )


def _bitmap_combined(node_type: str, *arms: dict[str, Any]) -> dict[str, Any]:
    return {
        "Node Type": "Bitmap Heap Scan",
        "Relation Name": "orders",
        "Schema": "public",
        "Alias": "orders",
        "Recheck Cond": "((orders.a = $1) OR (orders.deleted_at IS NULL))",
        "Filter": "(orders.email = $2)",
        "Plans": [{"Node Type": node_type, "Plans": list(arms)}],
    }


_WHOLE_ARM = {"Node Type": "Bitmap Index Scan", "Index Name": "o_live"}
_COND_ARM = {
    "Node Type": "Bitmap Index Scan",
    "Index Name": "o_a",
    "Index Cond": "(orders.a = $1)",
}


@pytest.mark.parametrize(
    "plan",
    [
        # a range filter: pagination-like, as for Index Scan (rule 2)
        _bitmap_whole_index("(orders.total > $1)"),
        _bitmap_whole_index(
            "(orders.created_at > $1)", recheck="(orders.deleted_at IS NULL)"
        ),
        # equalities on two columns
        _bitmap_whole_index(
            "((orders.status = $1) AND (orders.email = $2))",
            recheck="(orders.deleted_at IS NULL)",
        ),
        # the bitmap has a real index condition
        _bitmap_whole_index(
            "(orders.note = $1)",
            recheck="(orders.a = $2)",
            index_cond="(orders.a = $2)",
        ),
        _bitmap_whole_index("(orders.note = $1)", index_cond="(orders.a = $2)"),
        # BitmapOr and BitmapAnd combine several indexes; one arm reading a
        # whole partial index does not mean the heap is read in full
        _bitmap_combined("BitmapOr", _WHOLE_ARM, _COND_ARM),
        _bitmap_combined("BitmapAnd", _WHOLE_ARM, _COND_ARM),
        _bitmap_combined("BitmapAnd", _COND_ARM, _COND_ARM),
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


def _no_transaction_block() -> RuntimeError:
    """What pg8000 raises for SAVEPOINT outside a transaction block."""
    return RuntimeError(
        {
            "S": "ERROR",
            "C": "25P01",
            "M": "SAVEPOINT can only be used in transaction blocks",
        }
    )


def test_run_explain_uses_its_own_transaction_in_autocommit_outside_one() -> None:
    conn = FakeConnection(autocommit=True)
    conn.fail["SAVEPOINT"] = _no_transaction_block()
    assert run_explain(conn, "SELECT 1") == Explained(
        {"Node Type": "Seq Scan"}, "public"
    )
    assert conn.log[:2] == ["SAVEPOINT queryguard_explain", "BEGIN"]
    assert conn.log[-1] == "ROLLBACK"
    assert "ROLLBACK TO SAVEPOINT queryguard_explain" not in conn.log


def test_run_explain_keeps_a_manual_transaction_in_autocommit() -> None:
    # autocommit is the driver's flag: the application's own BEGIN is open
    conn = FakeConnection(autocommit=True)
    assert run_explain(conn, "SELECT 1") == Explained(
        {"Node Type": "Seq Scan"}, "public"
    )
    assert conn.log[0] == "SAVEPOINT queryguard_explain"
    assert "BEGIN" not in conn.log
    assert "ROLLBACK" not in conn.log
    assert conn.log[-2:] == [
        "ROLLBACK TO SAVEPOINT queryguard_explain",
        "RELEASE SAVEPOINT queryguard_explain",
    ]


def test_run_explain_in_autocommit_stops_on_another_savepoint_error() -> None:
    conn = FakeConnection(autocommit=True)
    conn.fail["SAVEPOINT"] = RuntimeError({"C": "08006", "M": "connection failure"})
    with pytest.raises(SavepointError, match="connection failure"):
        run_explain(conn, "SELECT 1")
    assert conn.log == ["SAVEPOINT queryguard_explain"]


def test_run_explain_outside_autocommit_never_falls_back_to_begin() -> None:
    conn = FakeConnection()
    conn.fail["SAVEPOINT"] = _no_transaction_block()
    with pytest.raises(SavepointError, match="transaction blocks"):
        run_explain(conn, "SELECT 1")
    assert conn.log == ["SAVEPOINT queryguard_explain"]


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


@pytest.mark.parametrize(
    ("autocommit", "undo"),
    [
        (
            False,
            [
                "ROLLBACK TO SAVEPOINT queryguard_explain",
                "RELEASE SAVEPOINT queryguard_explain",
            ],
        ),
        (True, ["ROLLBACK"]),
    ],
)
@pytest.mark.parametrize("error", [RuntimeError, MemoryError, RecursionError])
def test_run_explain_undoes_its_savepoint_when_it_has_a_bug(
    monkeypatch: pytest.MonkeyPatch,
    autocommit: bool,
    undo: list[str],
    error: type[Exception],
) -> None:
    # A bug past the SAVEPOINT (or BEGIN) must not leave the user's connection
    # inside the plugin's savepoint, transaction or local settings.
    def broken(*args: object) -> list[object]:
        raise error("find_scans broke")

    monkeypatch.setattr(indexes, "find_scans", broken)
    conn = FakeConnection(autocommit=autocommit)
    with pytest.raises(error, match="find_scans broke"):
        run_explain(conn, "SELECT 1", skip_partial=True)
    assert conn.log[-len(undo) :] == undo
    assert conn.closed_cursors == 1


def test_run_explain_raises_its_own_bug_when_the_undo_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*args: object) -> list[object]:
        raise RuntimeError("find_scans broke")

    monkeypatch.setattr(indexes, "find_scans", broken)
    conn = FakeConnection()
    conn.fail["ROLLBACK TO SAVEPOINT"] = RuntimeError("connection lost")
    with pytest.raises(RuntimeError, match="find_scans broke"):
        run_explain(conn, "SELECT 1", skip_partial=True)
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
    asyncpg = FakeDialect(driver="asyncpg", is_async=True, paramstyle="numeric_dollar")
    assert unsupported_reason(asyncpg) is None
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
            FakeDialect(driver="psycopg_async", is_async=True, paramstyle="pyformat"),
            (
                "statements from the async driver psycopg_async were not checked: "
                "the index check supports asyncpg and sync drivers only"
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


class FakeEngine:
    pass


class FakeSAConnection:
    """The parts of a SQLAlchemy Connection that check() uses."""

    def __init__(
        self,
        dialect: FakeDialect,
        result: Any = PLAN,
        engine: FakeEngine | None = None,
    ) -> None:
        self.dialect = dialect
        self.engine = engine or FakeEngine()
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


@pytest.mark.parametrize(
    "dialect",
    [
        FakeDialect(version=(16, 0)),
        FakeDialect(version=(18, 6)),
        FakeDialect(version=(15, 8)),  # unsupported_reason() speaks for it
        FakeDialect(version=None),
        FakeDialect(version=("x",)),  # type: ignore[arg-type]
        FakeDialect("sqlite", version=(99,)),
    ],
)
def test_no_version_notice_for_tested_or_unknown_versions(
    dialect: FakeDialect,
) -> None:
    assert version_notice(dialect) is None


def test_a_server_newer_than_the_tested_ones_gets_a_notice() -> None:
    assert version_notice(FakeDialect(version=(19, 0))) == (
        "PostgreSQL 19.0 is newer than the versions the index check is tested "
        "on (16 to 18): its statements are checked, but a finding may be wrong "
        "or missing; please report one that is"
    )


def test_check_explains_on_a_newer_server_and_records_one_notice() -> None:
    conn = FakeSAConnection(FakeDialect(version=(19, 1)), result=SEQ_PLAN)
    target = _collector()
    check(conn, "SELECT a FROM t WHERE a = %s", False, [target])
    check(conn, "SELECT b FROM t WHERE b = %s", False, [target])
    assert len([s for s in conn.raw.log if s.startswith("EXPLAIN")]) == 2
    assert len(target.scans) == 2
    [notice] = target.index_notices
    assert notice.startswith("PostgreSQL 19.1 is newer than the versions")


def test_check_records_explain_errors() -> None:
    conn = FakeSAConnection(FakeDialect())
    conn.raw.fail["EXPLAIN"] = RuntimeError("cannot plan")
    target = _collector()
    check(conn, "SELECT $1", False, [target])
    assert target.explain_errors == [("cannot plan", "SELECT $1")]
    assert target.scans == []


def test_check_records_a_statement_that_keeps_failing_once() -> None:
    # DDL forgets what was explained, so the statement is planned again.
    conn = FakeSAConnection(FakeDialect())
    conn.raw.fail["EXPLAIN"] = RuntimeError("cannot plan")
    target = _collector()
    check(conn, "SELECT $1", False, [target])
    target.explained.clear()
    check(conn, "SELECT $1", False, [target])
    check(conn, "SELECT $2", False, [target])
    assert target.explain_errors == [
        ("cannot plan", "SELECT $1"),
        ("cannot plan", "SELECT $2"),
    ]


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
    conn.fail["SELECT CASE"] = RuntimeError("no pg_index today")
    outcome = run_explain(conn, "SELECT 1", leading=True, catalog=Catalog())
    assert isinstance(outcome, Explained)
    assert (outcome.leading, outcome.varchar) == ({}, {})
    assert outcome.catalog_error == "no pg_index today"
    # the aborted lookup is not followed by the varchar one
    assert len([sql for sql in conn.log if sql.startswith("SELECT CASE")]) == 1
    assert conn.log[-2] == "ROLLBACK TO SAVEPOINT queryguard_explain"


def test_leading_lookup_reads_btree_indexes_only() -> None:
    # Rule 3 is about btree: BRIN and GIN indexes on (a, b) serve b = $1.
    plan = [{"Plan": _composite("Index Scan", "(orders.status = $1)")}]
    conn = FakeConnection(result=plan)
    run_explain(conn, "SELECT 1", leading=True, catalog=Catalog())
    [lookup] = [sql for sql in conn.log if "pg_index" in sql]
    assert "JOIN pg_am am ON am.oid = c.relam" in lookup
    assert "am.amname = 'btree'" in lookup


def test_temporary_tables_are_looked_up_by_the_connections_temp_schema() -> None:
    node = _composite("Index Scan", "(t.status = $1)")
    node["Schema"] = "pg_temp"
    node["Relation Name"] = "t"
    node["Index Name"] = "t_idx"
    node["Filter"] = "((t.name)::text = $1)"
    conn = FakeConnection(result=[{"Plan": node}])
    run_explain(conn, "SELECT 1", leading=True, catalog=Catalog())
    [lookup] = [sql for sql in conn.log if "pg_index" in sql]
    assert "n.oid = pg_my_temp_schema() AND c.relname IN ('t_idx')" in lookup
    assert "n.nspname, c.relname) IN" not in lookup
    [varchar] = [sql for sql in conn.log if "typname" in sql]
    assert "n.oid = pg_my_temp_schema() AND c.relname IN ('t')" in varchar


def test_temporary_tables_are_not_kept_in_the_session_cache() -> None:
    # pg_temp is a different schema on every connection.
    node = _composite("Index Scan", "(t.status = $1)")
    node["Schema"] = "pg_temp"
    node["Relation Name"] = "t"
    node["Index Name"] = "t_idx"
    node["Filter"] = "((t.name)::text = $1)"
    conn = FakeConnection(result=[{"Plan": node}])
    conn.leading_rows = [("pg_temp", "t_idx", "tenant_id")]
    conn.varchar_rows = [("pg_temp", "t", "name")]
    catalog = Catalog()
    outcome = run_explain(conn, "SELECT 1", leading=True, catalog=catalog)
    assert isinstance(outcome, Explained)
    assert outcome.leading == {("pg_temp", "t_idx"): "tenant_id"}
    assert outcome.varchar == {("pg_temp", "t"): frozenset({"name"})}
    assert (catalog.leading, catalog.varchar) == ({}, {})


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
    assert not [sql for sql in conn.log if sql.startswith("SELECT CASE")]


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


def test_failed_catalog_lookup_is_not_retried_in_the_session() -> None:
    plan = [{"Plan": _composite("Index Scan", "(orders.status = $1)")}]
    conn = FakeSAConnection(FakeDialect(), result=plan)
    conn.raw.fail["SELECT CASE"] = RuntimeError("permission denied for pg_index")
    first, second = _collector(), _collector()
    check(conn, "SELECT * FROM orders WHERE status = %s", False, [first])
    check(conn, "SELECT id FROM orders WHERE status = %s", False, [second])
    lookups = [sql for sql in conn.raw.log if sql.startswith("SELECT CASE")]
    assert len(lookups) == 1
    notice = (
        "the index check stopped reading the catalog for the rest of the session: "
        "permission denied for pg_index; trailing index columns and casts to "
        "text are not judged"
    )
    assert list(first.index_notices) == [notice]
    assert list(second.index_notices) == [notice]
    # explaining goes on
    assert len([sql for sql in conn.raw.log if sql.startswith("EXPLAIN")]) == 2


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
    assert _lookups(conn) == lookups
    assert len(target.scans) == lookups


def _lookups(conn: FakeSAConnection, table: str = "pg_index") -> int:
    # the partial-index lookup also reads pg_index; it is counted on its own
    return len(
        [sql for sql in conn.raw.log if table in sql and "indpred IS NOT" not in sql]
    )


def test_check_looks_up_each_index_once_per_session() -> None:
    plan = [{"Plan": _composite("Index Scan", "(orders.status = $1)")}]
    engine = FakeEngine()
    conn = FakeSAConnection(FakeDialect(), result=plan, engine=engine)
    conn.raw.leading_rows = [("public", "orders_tenant_id_status_idx", "tenant_id")]
    target = _collector()
    check(conn, "SELECT * FROM orders WHERE status = %s", False, [target])
    check(conn, "SELECT id FROM orders WHERE status = %s", False, [target])
    assert _lookups(conn) == 1
    assert len(target.scans) == 2
    # the next test, on another connection of the same engine
    other_conn = FakeSAConnection(FakeDialect(), result=plan, engine=engine)
    other = _collector()
    check(other_conn, "SELECT * FROM orders WHERE status = %s", False, [other])
    assert _lookups(other_conn) == 0
    assert len(other.scans) == 1
    # another engine looks it up again
    third = FakeSAConnection(FakeDialect(), result=plan)
    check(third, "SELECT * FROM orders WHERE status = %s", False, [_collector()])
    assert _lookups(third) == 1


def test_check_remembers_an_index_without_a_plain_first_column() -> None:
    plan = [{"Plan": _composite("Index Scan", "(orders.status = $1)")}]
    conn = FakeSAConnection(FakeDialect(), result=plan)
    first, second = _collector(), _collector()
    check(conn, "SELECT * FROM orders WHERE status = %s", False, [first])
    check(conn, "SELECT * FROM orders WHERE status = %s", False, [second])
    assert _lookups(conn) == 1
    assert first.scans == second.scans == []


def test_check_looks_up_varchar_columns_once_per_session() -> None:
    plan = [{"Plan": _seq(Filter="((orders.status)::text = $1)")}]
    conn = FakeSAConnection(FakeDialect(), result=plan)
    conn.raw.varchar_rows = [("public", "orders", "status")]
    for _ in range(3):
        target = _collector()
        check(conn, "SELECT * FROM orders WHERE status = %s", False, [target])
        assert target.scans[0].issue.column == "status"
    assert _lookups(conn, "pg_type") == 1


def test_check_looks_up_again_after_ddl() -> None:
    from pytest_queryguard import catalog

    plan = [{"Plan": _composite("Index Scan", "(orders.status = $1)")}]
    conn = FakeSAConnection(FakeDialect(), result=plan)
    conn.raw.leading_rows = [("public", "orders_tenant_id_status_idx", "tenant_id")]
    check(conn, "SELECT * FROM orders WHERE status = %s", False, [_collector()])
    catalog.observe(conn, "DROP INDEX orders_tenant_id_status_idx")
    conn.raw.leading_rows = [("public", "orders_tenant_id_status_idx", "status")]
    target = _collector()
    check(conn, "SELECT * FROM orders WHERE status = %s", False, [target])
    assert _lookups(conn) == 2
    assert target.scans == []


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
    from pytest_queryguard import catalog

    old = indexes.swap_stopped(None)
    old_catalog = indexes.swap_catalog_error(None)
    old_plpgsql = indexes.swap_no_plpgsql(indexes.EngineSet())
    catalog.reset()
    yield
    catalog.reset()
    indexes.swap_stopped(old)
    indexes.swap_catalog_error(old_catalog)
    indexes.swap_no_plpgsql(old_plpgsql)


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


def test_swap_catalog_error_returns_the_previous_state() -> None:
    assert indexes.swap_catalog_error("x") is None
    assert indexes.swap_catalog_error(None) == "x"


def test_swap_no_plpgsql_returns_the_previous_state() -> None:
    engine = FakeEngine()
    marked = indexes.EngineSet()
    marked.add(engine)
    empty = indexes.swap_no_plpgsql(marked)
    assert engine not in empty
    assert indexes.swap_no_plpgsql(empty) is marked
    assert engine in marked


def test_engine_set_falls_back_to_ids_for_engines_without_weakrefs() -> None:
    engines = indexes.EngineSet()
    plain = object()  # cannot be weakly referenced
    engines.add(plain)
    assert plain in engines
    assert object() not in engines


# ------------------------------------------------------------ async (asyncpg)


@pytest.mark.parametrize(
    ("sql", "sql_tag", "do_tag"),
    [
        ("SELECT 1", "$qg_sql$", "$qg_do$"),
        ("SELECT '$qg_sql$'", "$qg_sql_1$", "$qg_do$"),
        ("SELECT $qg_sql$x$qg_sql$, $qg_sql_1$y$qg_sql_1$", "$qg_sql_2$", "$qg_do$"),
        ("SELECT '$qg_do$'", "$qg_sql$", "$qg_do_1$"),
        # a tag that would close early when the statement ends in its prefix
        ("SELECT 1 AS $qg_sql", "$qg_sql_1$", "$qg_do$"),
        ("SELECT $$a$$, 'it''s', \"q\"", "$qg_sql$", "$qg_do$"),
    ],
)
def test_explain_block_quotes_the_statement_with_free_tags(
    sql: str, sql_tag: str, do_tag: str
) -> None:
    block = explain_block(sql)
    explain = f"EXPLAIN (GENERIC_PLAN, VERBOSE, FORMAT JSON) {sql}"
    assert block == (
        f"DO {do_tag}DECLARE queryguard_plan text; BEGIN "
        f"EXECUTE {sql_tag}{explain}{sql_tag} INTO queryguard_plan; "
        "PERFORM set_config('queryguard.plan', queryguard_plan, true); "
        f"END{do_tag}"
    )


class FakeAsyncpg:
    """The parts of an asyncpg connection the async path uses."""

    def __init__(self, owner: FakeAdaptedAsyncpg) -> None:
        self.owner = owner

    async def execute(self, sql: str) -> str:
        self.owner.log.append(("execute", sql))
        self.owner.maybe_fail(sql)
        return "OK"

    async def fetch(self, sql: str, *args: Any) -> list[tuple[Any, ...]]:
        self.owner.log.append(("fetch", sql))
        self.owner.args.append(args)
        self.owner.maybe_fail(sql)
        if sql.startswith("SELECT set_config"):
            return [("off", "1s", "5s", "public")]
        if sql == "SELECT current_setting('queryguard.plan')":
            return [(self.owner.plan,)]
        if "pg_index" in sql:
            return self.owner.leading_rows
        return []


class FakeAdaptedAsyncpg:
    """SQLAlchemy's adapted asyncpg connection: run_async() and autocommit."""

    def __init__(self, plan: Any = None, *, autocommit: bool = False) -> None:
        self.autocommit = autocommit
        self.plan = json.dumps(PLAN if plan is None else plan)
        self.log: list[tuple[str, str]] = []
        self.fail: dict[str, Exception] = {}
        self.leading_rows: list[tuple[str, str, str]] = []
        self.args: list[tuple[Any, ...]] = []

    def maybe_fail(self, sql: str) -> None:
        for prefix, exc in self.fail.items():
            if sql.startswith(prefix):
                raise exc

    def run_async(self, fn: Any) -> Any:
        coroutine = fn(FakeAsyncpg(self))
        try:
            coroutine.send(None)
        except StopIteration as done:
            return done.value
        raise AssertionError("the fake coroutine did not finish")  # pragma: no cover

    def cursor(self) -> Any:
        raise AssertionError("the async path must not use a DB-API cursor")


def test_run_explain_on_asyncpg_runs_explain_in_a_do_block() -> None:
    conn = FakeAdaptedAsyncpg()
    outcome = run_explain(conn, "SELECT a FROM t WHERE a = $1", asyncpg=True)
    assert outcome == Explained({"Node Type": "Seq Scan"}, "public")
    assert conn.log == [
        ("execute", "SAVEPOINT queryguard_explain"),
        ("fetch", indexes._SETTINGS),
        ("execute", explain_block("SELECT a FROM t WHERE a = $1")),
        ("fetch", "SELECT current_setting('queryguard.plan')"),
        ("execute", "ROLLBACK TO SAVEPOINT queryguard_explain"),
        ("execute", "RELEASE SAVEPOINT queryguard_explain"),
    ]


class _SqlstateError(Exception):
    """asyncpg's errors carry the SQLSTATE as an attribute."""

    sqlstate = "25P01"


def test_run_explain_on_asyncpg_in_autocommit_outside_a_transaction() -> None:
    conn = FakeAdaptedAsyncpg(autocommit=True)
    conn.fail["SAVEPOINT"] = _SqlstateError(
        "SAVEPOINT can only be used in transaction blocks"
    )
    run_explain(conn, "SELECT 1", asyncpg=True)
    assert conn.log[:2] == [
        ("execute", "SAVEPOINT queryguard_explain"),
        ("execute", "BEGIN"),
    ]
    assert conn.log[-1] == ("execute", "ROLLBACK")


def test_run_explain_on_asyncpg_keeps_a_manual_transaction_in_autocommit() -> None:
    conn = FakeAdaptedAsyncpg(autocommit=True)
    run_explain(conn, "SELECT 1", asyncpg=True)
    assert conn.log[0] == ("execute", "SAVEPOINT queryguard_explain")
    assert ("execute", "BEGIN") not in conn.log
    assert ("execute", "ROLLBACK") not in conn.log
    assert conn.log[-1] == ("execute", "RELEASE SAVEPOINT queryguard_explain")


def test_run_explain_on_asyncpg_reports_a_failed_explain() -> None:
    conn = FakeAdaptedAsyncpg()
    conn.fail["DO "] = RuntimeError(
        "could not determine data type of parameter $1\nCONTEXT:  PL/pgSQL ..."
    )
    outcome = run_explain(conn, "SELECT $1", asyncpg=True)
    assert outcome == ExplainError("could not determine data type of parameter $1")
    assert conn.log[-2:] == [
        ("execute", "ROLLBACK TO SAVEPOINT queryguard_explain"),
        ("execute", "RELEASE SAVEPOINT queryguard_explain"),
    ]


def test_run_explain_on_asyncpg_raises_when_the_savepoint_breaks() -> None:
    conn = FakeAdaptedAsyncpg()
    conn.fail["ROLLBACK TO SAVEPOINT"] = RuntimeError("connection lost")
    with pytest.raises(SavepointError, match="connection lost"):
        run_explain(conn, "SELECT 1", asyncpg=True)


def test_run_explain_on_asyncpg_reads_the_catalog() -> None:
    plan = [{"Plan": _composite("Index Scan", "(orders.status = $1)")}]
    conn = FakeAdaptedAsyncpg(plan)
    conn.leading_rows = [("public", "orders_tenant_id_status_idx", "tenant_id")]
    outcome = run_explain(
        conn, "SELECT 1", leading=True, catalog=Catalog(), asyncpg=True
    )
    assert isinstance(outcome, Explained)
    assert outcome.leading == LEADING
    assert [kind for kind, sql in conn.log if "pg_index" in sql] == ["fetch"]
    # constant text, names as array parameters: one statement in asyncpg's cache
    [lookup] = [sql for _, sql in conn.log if "pg_index" in sql]
    assert "$1::text[]" in lookup and "'orders_tenant" not in lookup
    assert ([], [], []) not in conn.args
    assert (["public"], ["orders_tenant_id_status_idx"], []) in conn.args


def test_asyncpg_lookups_keep_constant_text_and_pass_temporary_names_apart() -> None:
    first = indexes._leading_lookup(
        {("public", "a_idx"), ("pg_temp", "t_idx")}, asyncpg=True
    )
    second = indexes._leading_lookup({("other", "b_idx")}, asyncpg=True)
    assert first is not None and second is not None
    assert first[0] == second[0]  # one text, whatever the names
    assert "pg_my_temp_schema()" in first[0] and "$3::text[]" in first[0]
    assert first[1] == (["public"], ["a_idx"], ["t_idx"])
    assert second[1] == (["other"], ["b_idx"], [])
    only_temp = indexes._varchar_lookup({("pg_temp", "t")}, asyncpg=True)
    assert only_temp is not None
    assert only_temp[1] == ([], [], ["t"])
    assert indexes._varchar_lookup({("public", "bad\\name")}, asyncpg=True) is None


class FakeAsyncSAConnection:
    def __init__(self, plan: Any) -> None:
        self.dialect = FakeDialect(
            driver="asyncpg", is_async=True, paramstyle="numeric_dollar"
        )
        self.engine = FakeEngine()
        self.raw = FakeAdaptedAsyncpg(plan)
        self.connection = FakeProxy(self.raw)  # type: ignore[arg-type]


def test_check_explains_statements_from_asyncpg() -> None:
    conn = FakeAsyncSAConnection(SEQ_PLAN)
    target = _collector()
    sql = "SELECT orders.id FROM orders WHERE orders.customer_id = $1::INTEGER"
    check(conn, sql, False, [target])
    assert ("execute", explain_block(sql)) in conn.raw.log
    [scan] = target.scans
    assert scan.issue.column == "customer_id"
    assert target.index_notices == {}


def test_check_reports_missing_plpgsql_once_and_keeps_sync_engines() -> None:
    conn = FakeAsyncSAConnection(SEQ_PLAN)
    conn.raw.fail["DO "] = RuntimeError('language "plpgsql" does not exist')
    target = _collector()
    check(conn, "SELECT a FROM t WHERE a = $1", False, [target])
    check(conn, "SELECT b FROM t WHERE b = $1", False, [target])
    assert target.explain_errors == []
    assert list(target.index_notices) == [indexes.NO_PLPGSQL_NOTICE]
    # the second statement was not sent at all
    assert len([1 for kind, sql in conn.raw.log if sql.startswith("DO ")]) == 1
    # a sync connection is still explained
    sync = FakeSAConnection(FakeDialect(), result=SEQ_PLAN)
    check(sync, "SELECT a FROM t WHERE a = $1", False, [target])
    assert any(sql.startswith("EXPLAIN") for sql in sync.raw.log)


def test_missing_plpgsql_on_one_asyncpg_engine_does_not_skip_another() -> None:
    broken, working = FakeAsyncSAConnection(SEQ_PLAN), FakeAsyncSAConnection(SEQ_PLAN)
    broken.raw.fail["DO "] = RuntimeError('language "plpgsql" does not exist')
    target = _collector()
    check(broken, "SELECT a FROM t WHERE a = $1", False, [target])
    check(working, "SELECT b FROM t WHERE b = $1", False, [target])
    assert any(sql.startswith("DO ") for _, sql in working.raw.log)
    assert [scan.issue.table for scan in target.scans] == ["orders"]
    assert target.explain_errors == []
    assert list(target.index_notices) == [indexes.NO_PLPGSQL_NOTICE]


@pytest.fixture
def engines_share_an_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every FakeEngine the id CPython may hand to a successor.

    CPython reuses the address of a collected object, and id() is the address.
    Forcing it keeps the tests deterministic.
    """
    real_id = id

    def shared_id(obj: object) -> int:
        return 1 if isinstance(obj, FakeEngine) else real_id(obj)

    monkeypatch.setattr(indexes, "id", shared_id, raising=False)


def test_a_new_engine_with_a_reused_id_is_not_marked_as_missing_plpgsql(
    engines_share_an_id: None,
) -> None:
    broken = FakeAsyncSAConnection(SEQ_PLAN)
    broken.raw.fail["DO "] = RuntimeError('language "plpgsql" does not exist')
    target = _collector()
    check(broken, "SELECT a FROM t WHERE a = $1", False, [target])
    del broken
    gc.collect()
    working = FakeAsyncSAConnection(SEQ_PLAN)
    check(working, "SELECT b FROM t WHERE b = $1", False, [target])
    assert any(sql.startswith("DO ") for _, sql in working.raw.log)
    assert [scan.issue.table for scan in target.scans] == ["orders"]


def test_a_new_engine_with_a_reused_id_is_explained_again(
    engines_share_an_id: None,
) -> None:
    first = FakeSAConnection(FakeDialect(), result=SEQ_PLAN)
    target = _collector()
    sql = "SELECT a FROM t WHERE a = %s"
    check(first, sql, False, [target])
    del first
    gc.collect()
    second = FakeSAConnection(FakeDialect(), result=SEQ_PLAN)
    check(second, sql, False, [target])
    assert any(s.startswith("EXPLAIN") for s in second.raw.log)


class _Sqlstate(Exception):
    def __init__(self, message: str, sqlstate: str) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


def test_missing_plpgsql_is_found_by_sqlstate_in_any_language() -> None:
    conn = FakeAsyncSAConnection(SEQ_PLAN)
    conn.raw.fail["DO "] = _Sqlstate('język "plpgsql" nie istnieje', "42704")
    target = _collector()
    check(conn, "SELECT a FROM t WHERE a = $1", False, [target])
    check(conn, "SELECT b FROM t WHERE b = $1", False, [target])
    assert target.explain_errors == []
    assert list(target.index_notices) == [indexes.NO_PLPGSQL_NOTICE]
    assert len([1 for _, sql in conn.raw.log if sql.startswith("DO ")]) == 1


def test_another_undefined_object_is_an_ordinary_explain_error() -> None:
    # 42704 also covers a missing type or operator in the user's statement.
    conn = FakeAsyncSAConnection(SEQ_PLAN)
    conn.raw.fail["DO "] = _Sqlstate('type "nope" does not exist', "42704")
    target = _collector()
    check(conn, "SELECT a FROM t WHERE a = $1", False, [target])
    assert target.explain_errors == [
        ('type "nope" does not exist', "SELECT a FROM t WHERE a = $1")
    ]
    assert target.index_notices == {}


# ----------------------------------------------------------- catalog clearing


class _ClearsWhileIterated(dict[Any, Any]):
    """Clears the cache once its items are being read, like another thread."""

    def items(self) -> Any:  # type: ignore[override]
        entries = super().items()

        def walk() -> Iterator[Any]:
            iterator = iter(entries)
            yield next(iterator)
            catalogs.clear()
            yield from iterator

        return walk()


def test_clearing_the_cache_while_a_plan_is_read_keeps_the_lookups() -> None:
    class Engine:
        pass

    plan = {
        "Node Type": "Index Scan",
        "Schema": "public",
        "Relation Name": "t",
        "Index Name": "t_idx",
        "Index Cond": "(t.b = $1)",
        "Plans": [
            {
                "Node Type": "Index Scan",
                "Schema": "public",
                "Relation Name": "u",
                "Index Name": "u_idx",
                "Index Cond": "(u.b = $1)",
            }
        ],
    }
    engine = Engine()
    entry = catalogs.for_engine(engine)
    entry.leading = _ClearsWhileIterated(
        {("public", "t_idx"): "a", ("public", "u_idx"): "a"}
    )
    try:
        leading, _ = indexes._read_catalog(FakeConnection().cursor(), plan, entry, True)
    finally:
        catalogs.reset()
    assert leading == {("public", "t_idx"): "a", ("public", "u_idx"): "a"}


class _ClearsAfterSelect:
    """Answers the catalog queries, then clears the cache as another thread
    would when its DDL commits between the SELECT and the write."""

    def __init__(self, leading: list[Any], varchar: list[Any]) -> None:
        self.leading = leading
        self.varchar = varchar
        self.rows: list[Any] = []

    def execute(self, sql: str) -> None:
        self.rows = self.leading if "pg_index" in sql else self.varchar
        catalogs.clear()

    def fetchall(self) -> list[Any]:
        return self.rows


@pytest.mark.parametrize("text_cast", [True, False])
def test_rows_read_before_a_clear_are_not_written_after_it(text_cast: bool) -> None:
    class Engine:
        pass

    plan = {
        "Node Type": "Index Scan",
        "Schema": "public",
        "Relation Name": "t",
        "Index Name": "t_idx",
        "Index Cond": "(t.b = $1)",
    }
    if text_cast:
        plan["Filter"] = "((t.s)::text = $2)"
    engine = Engine()  # held: the cache only keeps engines that are alive
    entry = catalogs.for_engine(engine)
    cursor = _ClearsAfterSelect([("public", "t_idx", "a")], [("public", "t", "s")])
    try:
        leading, varchar = indexes._read_catalog(cursor, plan, entry, True)
    finally:
        catalogs.reset()
    # What was read still answers this call, but none of it is cached.
    assert leading == {("public", "t_idx"): "a"}
    assert varchar == ({("public", "t"): frozenset({"s"})} if text_cast else {})
    assert entry.leading == {}
    assert entry.varchar == {}


def test_a_walked_index_is_named_by_every_rule_that_reports_one() -> None:
    # rule 2, an index scan with a filter and no index condition
    [whole] = find_scans(_pkey_scan("(orders.customer_id = $1)"))
    assert whole.index == "index orders_pkey"
    # rule 2 again, a whole index read as a bitmap
    [bitmap] = find_scans(_bitmap_whole_index("(orders.note = $1)"))
    assert bitmap.index == "index o_inc"
    # rule 3, an index condition on a trailing column
    [trailing] = find_scans(
        _composite("Bitmap Index Scan", "(orders.status = $1)"), None, LEADING
    )
    assert trailing.index == "index orders_tenant_id_status_idx"


def test_a_sequential_scan_names_no_index() -> None:
    [issue] = find_scans(_seq(Filter="(orders.customer_id = $1)"))
    assert issue.index is None
