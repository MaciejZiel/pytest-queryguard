"""The index check on real EXPLAIN output from PostgreSQL 14 to 18.

tests/fixtures/plans/pg<major>/<name>.json was captured by
scripts/capture_plans.py from a live server: EXPLAIN (GENERIC_PLAN, VERBOSE,
FORMAT JSON) on 16 to 18, and the same generic plan of a prepared statement on
14 and 15. Next to the plan each fixture keeps what the check read from the
catalog for it (``catalog``) and, on 16 and newer, what the check itself
reported on that server (``live``). These tests need no database: they replay
the plan walk, the partial-index rule and the advisor offline and compare.

The scenarios cover the predicate shapes, joins, sub-queries, CTEs, LATERAL,
JSONB, arrays, ORDER BY with LIMIT, DISTINCT, window functions, UPDATE and
DELETE, partial and expression indexes, partitioned and inherited tables,
temporary and unlogged tables, row level security, schemas and mixed-case
names. To add one, add it to QUERIES in the script, capture it on every
version and add its expected findings here.
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterator
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from pytest_queryguard import advisor, indexes, partial, predicates
from pytest_queryguard.events import ScanIssue
from pytest_queryguard.indexes import find_scans

PLANS = Path(__file__).parent / "fixtures" / "plans"
VERSIONS = [14, 15, 16, 17, 18]
CONDITION_KEYS = (
    "Filter",
    "Index Cond",
    "Recheck Cond",
    "Join Filter",
    "Hash Cond",
    "Merge Cond",
    "One-Time Filter",
)
SEQ = "Seq Scan"

# name -> [(table, node, kind, column, expression)], the same on every version
# unless EXPECTED_BY_VERSION says otherwise. A partitioned table is reported
# once per partition (#86); an inherited table once per child, which is right:
# an index on the parent does not cover them.
EXPECTED: dict[str, list[tuple[str, str, str, str | None, bool]]] = {
    "aggregate_filter": [("orders", SEQ, "filter", "customer_id", False)],
    "any_array_literal": [("orders", SEQ, "filter", "customer_id", False)],
    "any_array_pk": [],
    "arithmetic": [("orders", SEQ, "filter", None, False)],
    "array_any": [("orders", SEQ, "filter", None, False)],
    "array_contains_gin": [],
    "array_contains_unindexed": [("orders", SEQ, "filter", None, False)],
    "array_length": [("orders", SEQ, "filter", None, True)],
    "array_overlap_gin": [],
    "array_subscript": [("orders", SEQ, "filter", None, True)],
    "at_time_zone": [("orders", SEQ, "filter", None, True)],
    "between": [("orders", SEQ, "filter", "total", False)],
    "boolean_column": [("posts", SEQ, "filter", None, False)],
    "boolean_is_true": [("posts", SEQ, "filter", None, False)],
    "boolean_not": [],
    "case_expression": [("orders", SEQ, "filter", None, True)],
    "cast_date": [("orders", SEQ, "filter", None, True)],
    "char_cast": [("orders", SEQ, "filter", "kind", False)],
    "coalesce": [("orders", SEQ, "filter", None, True)],
    "collate": [("customers", SEQ, "filter", "name", False)],
    "concat": [("customers", SEQ, "filter", None, False)],
    "correlated_subplan": [("orders", SEQ, "filter", "customer_id", False)],
    "cte": [("orders", SEQ, "filter", "customer_id", False)],
    "cte_data_modifying": [("orders", SEQ, "filter", "status", False)],
    "cte_materialized": [("orders", SEQ, "filter", "customer_id", False)],
    "cte_recursive": [],
    "current_date": [("orders", SEQ, "filter", None, True)],
    "date_trunc": [("orders", SEQ, "filter", None, True)],
    "delete_in_subquery": [
        ("orders", "Index Scan using orders_pkey", "filter", "status", False)
    ],
    "delete_using": [
        ("customers", "Index Scan using customers_pkey", "filter", "name", False)
    ],
    "distinct": [("orders", SEQ, "filter", "customer_id", False)],
    "distinct_on": [("orders", SEQ, "filter", "total", False)],
    "eq_and_range": [("orders", SEQ, "filter", None, False)],
    "eq_any_array": [("orders", SEQ, "filter", "customer_id", False)],
    "eq_in_list": [("orders", SEQ, "filter", "customer_id", False)],
    "eq_param": [("orders", SEQ, "filter", "customer_id", False)],
    "exists_semi_join": [("orders", SEQ, "filter", "total", False)],
    "expression_index_match": [],
    "expression_index_other_function": [("posts", SEQ, "filter", None, True)],
    "extract_year": [("orders", SEQ, "filter", None, True)],
    "full_join": [("orders", SEQ, "filter", "status", False)],
    "function_lower": [("customers", SEQ, "filter", None, True)],
    "greatest": [("orders", SEQ, "filter", None, True)],
    "group_by_filter": [("orders", SEQ, "filter", "customer_id", False)],
    "group_by_having": [],
    "ilike": [("customers", SEQ, "filter", None, False)],
    "in_list_literals": [("orders", SEQ, "filter", "status", False)],
    "in_subquery": [("orders", SEQ, "filter", "total", False)],
    "inheritance_child": [("cars", SEQ, "filter", "seats", False)],
    "inheritance_only": [("vehicles", SEQ, "filter", "plate", False)],
    "inheritance_parent": [
        ("vehicles", SEQ, "filter", "owner_id", False),
        ("cars", SEQ, "filter", "owner_id", False),
        ("trucks", SEQ, "filter", "owner_id", False),
    ],
    "initplan": [
        ("orders", SEQ, "filter", "customer_id", False),
        ("customers", SEQ, "filter", "email", False),
    ],
    "is_distinct_from": [("orders", SEQ, "filter", None, False)],
    "is_not_null": [("orders", SEQ, "filter", None, False)],
    "is_null": [("orders", SEQ, "filter", None, False)],
    "join_inequality": [("customers", SEQ, "filter", "email", False)],
    "join_orders_customers": [("orders", SEQ, "filter", "customer_id", False)],
    "json_arrow": [("orders", SEQ, "filter", None, False)],
    "jsonb_arrow_cast": [("docs", SEQ, "filter", None, False)],
    "jsonb_contains_gin": [],
    "jsonb_contains_unindexed": [("orders", SEQ, "filter", None, False)],
    "jsonb_key_exists_gin": [],
    "jsonb_nested_arrow": [("docs", SEQ, "filter", None, False)],
    "lateral": [
        ("customers", SEQ, "filter", "email", False),
        ("orders", SEQ, "filter", "customer_id", False),
    ],
    "leading_index_column": [],
    "left_join_alias": [
        ("customers", "Index Scan using customers_pkey", "filter", "email", False)
    ],
    "left_join_is_null": [],
    "like": [("customers", SEQ, "filter", None, False)],
    "like_literal_prefix": [("customers", SEQ, "filter", None, False)],
    "limit_offset": [
        ("orders", "Index Scan using orders_pkey", "filter", "status", False)
    ],
    "literal_backslash": [("orders", SEQ, "filter", None, False)],
    "literal_constant": [("orders", SEQ, "filter", "status", False)],
    "literal_quote": [("orders", SEQ, "filter", "note", False)],
    "mixed_case_order_by": [("Mixed Case", SEQ, "filter", '"customerId"', False)],
    "mixed_case_schema": [("Order Lines", SEQ, "filter", '"orderId"', False)],
    "multicolumn_leading_order": [],
    "multicolumn_trailing_range": [],
    "ne_all_array": [("orders", SEQ, "filter", None, False)],
    "not_condition": [("orders", SEQ, "filter", None, False)],
    "not_equal": [("orders", SEQ, "filter", None, False)],
    "not_exists_anti_join": [],
    "not_in_list": [("orders", SEQ, "filter", None, False)],
    "not_in_subquery": [
        ("customers", SEQ, "filter", None, False),
        ("orders", SEQ, "filter", "status", False),
    ],
    "now_minus_interval": [("orders", SEQ, "filter", "created_at", False)],
    "nullif": [("orders", SEQ, "filter", None, True)],
    "numeric_param_cast": [("orders", SEQ, "filter", "total", False)],
    "or_same_column": [("orders", SEQ, "filter", "customer_id", False)],
    "or_two_columns": [("orders", SEQ, "filter", None, False)],
    "order_by_no_filter": [],
    "order_by_with_equality": [("orders", SEQ, "filter", "customer_id", False)],
    "order_nulls_first_no_filter": [],
    "order_nulls_last_limit": [("orders", SEQ, "filter", "customer_id", False)],
    "partial_index_literal": [],
    "partial_index_match": [],
    "partial_index_other_column": [("archive", SEQ, "filter", "owner_id", False)],
    "partial_index_parameter": [("posts", SEQ, "filter", None, False)],
    "partition_indexed": [],
    "partition_key_literal": [("events_2026", SEQ, "filter", None, False)],
    "partition_key_param": [
        ("events_2025", SEQ, "filter", None, False),
        ("events_2026", SEQ, "filter", None, False),
    ],
    "partition_order_limit": [
        ("events_2025", SEQ, "filter", "kind", False),
        ("events_2026", SEQ, "filter", "kind", False),
    ],
    "partition_unindexed": [
        ("events_2025", SEQ, "filter", "kind", False),
        ("events_2026", SEQ, "filter", "kind", False),
    ],
    "quoted_alias": [("Mixed Case", SEQ, "filter", '"customerId"', False)],
    "quoted_identifiers": [("Mixed Case", SEQ, "filter", '"customerId"', False)],
    "range_one_sided": [("orders", SEQ, "filter", "total", False)],
    "range_two_sided": [("orders", SEQ, "filter", "total", False)],
    "regex_match": [("customers", SEQ, "filter", None, False)],
    "row_comparison": [("orders", SEQ, "filter", None, False)],
    "row_level_security": [("tenant_rows", SEQ, "filter", None, False)],
    "schema_qualified": [("invoices", SEQ, "filter", "customer_id", False)],
    "schema_qualified_join": [("invoices", SEQ, "filter", "amount", False)],
    "self_join": [],
    "similar_to": [("customers", SEQ, "filter", None, False)],
    "subquery_in_from": [("orders", SEQ, "filter", None, False)],
    "substring": [("customers", SEQ, "filter", None, True)],
    "temp_table": [("scratch", SEQ, "filter", "ref", False)],
    "text_search": [("orders", SEQ, "filter", None, True)],
    "three_way_join": [
        ("customers", "Index Scan using customers_pkey", "filter", "email", False)
    ],
    "trailing_index_column": [
        (
            "order_items",
            "Bitmap Index Scan using order_items_order_product",
            "index condition",
            "product_id",
            False,
        )
    ],
    "two_equalities": [("orders", SEQ, "filter", None, False)],
    "union": [
        ("orders", "Index Scan using orders_pkey", "filter", "status", False),
        ("orders", "Index Scan using orders_pkey", "filter", "customer_id", False),
    ],
    "unlogged_table": [("cache_entries", SEQ, "filter", "key", False)],
    "unnest_in": [],
    "update_from": [
        ("customers", "Index Scan using customers_pkey", "filter", "name", False)
    ],
    "update_in_subquery": [
        ("customers", "Index Scan using customers_pkey", "filter", "email", False)
    ],
    "values_join": [],
    "varchar_cast": [("orders", SEQ, "filter", "status", False)],
    "whole_index_order_limit": [
        ("orders", "Index Scan using orders_pkey", "filter", "customer_id", False)
    ],
    "window_function": [("orders", SEQ, "filter", "status", False)],
}

EXPECTED_BY_VERSION: dict[tuple[str, int], list[Any]] = {
    # PostgreSQL 18 skips the leading column of the index, so the check does
    # not judge a condition on the trailing one.
    ("trailing_index_column", 18): [],
    # Before 16 the plan of a prepared statement prunes the 2025 partition at
    # plan time; the generic plan of 16 and newer keeps both.
    ("partition_key_param", 14): [("events_2026", SEQ, "filter", None, False)],
    ("partition_key_param", 15): [("events_2026", SEQ, "filter", None, False)],
    # Before 17 the planner reads both halves of the UNION in full; from 17 it
    # walks the primary key for the sorted UNION.
    **{
        ("union", version): [
            ("orders", SEQ, "filter", "status", False),
            ("orders", SEQ, "filter", "customer_id", False),
        ]
        for version in (14, 15, 16)
    },
}

# Conditions the old text matching read differently from the parsed tree; the
# tree is right for each of them (see test_predicates.py).
TEXT_MATCHING_WRONG = {
    "case_expression",  # #84: a CASE is no plain comparison
    "at_time_zone",  # #84: AT TIME ZONE wraps the column
    "array_subscript",  # so does a subscript
    "not_in_subquery",  # #83: NOT (ANY (...)) on 17 and newer
    "row_comparison",  # #85
}


@cache
def document(version: int, name: str) -> dict[str, Any]:
    return json.loads((PLANS / f"pg{version}" / f"{name}.json").read_text())


def plan_of(version: int, name: str) -> dict[str, Any]:
    return document(version, name)["plan"][0]["Plan"]


def nodes(plan: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield plan
    for child in plan.get("Plans", []):
        yield from nodes(child)


def catalog(
    version: int, name: str
) -> tuple[dict[Any, str], dict[Any, frozenset[str]]]:
    found = document(version, name)["catalog"]
    leading = {(s, i): c for s, i, c in found["leading"]}
    varchar = {(s, t): frozenset(c) for s, t, c in found["varchar"]}
    return leading, varchar


def replay(version: int, name: str) -> list[ScanIssue]:
    leading, varchar = catalog(version, name)
    return find_scans(plan_of(version, name), "public", leading, varchar)


def test_every_version_has_every_fixture() -> None:
    names = {p.stem for p in (PLANS / "pg17").glob("*.json")}
    assert names == set(EXPECTED)
    for version in VERSIONS:
        assert {p.stem for p in (PLANS / f"pg{version}").glob("*.json")} == names


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_find_scans_on_real_plans(version: int, name: str) -> None:
    found = [
        (i.table, i.node, i.kind, i.column, i.expression) for i in replay(version, name)
    ]
    assert found == EXPECTED_BY_VERSION.get((name, version), EXPECTED[name])


def _offline(version: int, name: str) -> list[dict[str, Any]]:
    """What run_explain() reports for the fixture, from its plan and catalog."""
    found = document(version, name)["catalog"]
    by_table: dict[tuple[str, str], list[partial.PartialIndex]] = {}
    for schema, table, index, predicate, columns in found["partial"]:
        by_table.setdefault((schema, table), []).append(
            partial.PartialIndex(schema, table, index, predicate, frozenset(columns))
        )
    existing: dict[tuple[str, str], list[advisor.ExistingIndex]] = {}
    for schema, table, index, method, valid, columns, predicate in found["indexes"]:
        existing.setdefault((schema, table), []).append(
            advisor.ExistingIndex(index, method, valid, tuple(columns), predicate)
        )
    _, varchar = catalog(version, name)
    kept, skipped = [], []
    for issue in replay(version, name):
        indexes_ = by_table.get((issue.schema or "", issue.table))
        if indexes_ and partial.may_serve(indexes_, issue.conditions, issue.alias):
            skipped.append(issue)
        else:
            kept.append(issue)
    rows = []
    for issue, was_skipped in [(i, False) for i in kept] + [(i, True) for i in skipped]:
        create_sql = reason = None
        if not was_skipped and issue.schema:
            key = (issue.schema, issue.table)
            analysis = advisor.analyse(issue, varchar.get(key, frozenset()))
            if analysis is not None and analysis.plain is not None:
                advice = advisor.make_advice(
                    issue,
                    analysis.plain,
                    indexes.table_label(issue),
                    existing.get(key, []),
                )
                create_sql, reason = advice.create_sql, advice.skipped
        rows.append(
            {
                "schema": issue.schema,
                "table": issue.table,
                "node": issue.node,
                "kind": issue.kind,
                "column": issue.column,
                "expression": issue.expression,
                "create_sql": create_sql,
                "skipped": reason,
                "partial_skipped": was_skipped,
            }
        )
    return rows


@pytest.mark.parametrize("version", [v for v in VERSIONS if v >= 16])
@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_offline_replay_matches_what_the_check_reported_on_the_server(
    version: int, name: str
) -> None:
    live = document(version, name)["live"]
    assert _offline(version, name) == live


@pytest.mark.parametrize("version", VERSIONS)
def test_every_condition_of_every_plan_is_parsed(version: int) -> None:
    """No condition in the corpus falls back to text matching."""
    unparsed = []
    for name in EXPECTED:
        for node in nodes(plan_of(version, name)):
            for key in CONDITION_KEYS:
                if key in node and predicates.parse(node[key]) is None:
                    unparsed.append((name, key, node[key]))
            for key in ("Sort Key", "Presorted Key"):
                for text in node.get(key, []):
                    match = advisor._SORT_KEY.fullmatch(text)
                    if match is None or predicates.parse(match["ref"]) is None:
                        unparsed.append((name, key, text))
    assert unparsed == []


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_conditions_of_real_plans_agree_with_text_matching(
    version: int, name: str
) -> None:
    """The text matching is only the fallback, but agrees where it is right."""
    if name in TEXT_MATCHING_WRONG:
        pytest.skip("the text matching is wrong here; the parsed tree decides")
    for node in nodes(plan_of(version, name)):
        alias = node.get("Alias", "")
        for key in CONDITION_KEYS:
            condition = node.get(key)
            if condition is None:
                continue
            for varchar in (frozenset(), frozenset({"status", "name", "email"})):
                assert indexes._comparisons(condition, alias, varchar) == (
                    indexes._regex_comparisons(condition, alias, varchar)
                ), condition
                assert indexes._needs_expression(condition, alias, varchar) == (
                    indexes._regex_needs_expression(condition, alias, varchar)
                ), condition


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_plans_of_all_versions_agree_on_the_findings(name: str) -> None:
    results = {
        version: tuple((i.table, i.column, i.kind) for i in replay(version, name))
        for version in VERSIONS
    }
    differ = {v for v in VERSIONS if (name, v) in EXPECTED_BY_VERSION}
    same = {results[v] for v in VERSIONS if v not in differ}
    assert len(same) == 1


@pytest.mark.parametrize("version", VERSIONS)
def test_structured_fields_name_what_the_check_reads(version: int) -> None:
    # Relation, schema, alias and index come as fields; the conditions are text.
    plan = plan_of(version, "left_join_alias")
    scans = [n for n in nodes(plan) if "Relation Name" in n]
    assert {(n["Relation Name"], n["Schema"], n["Alias"]) for n in scans} == {
        ("customers", "public", "customers"),
        ("orders", "public", "o2"),
    }
    assert all(isinstance(n.get("Output", []), list) for n in scans)
    conditions = [n[k] for n in nodes(plan) for k in CONDITION_KEYS if k in n]
    assert conditions and all(isinstance(c, str) for c in conditions)


@pytest.mark.parametrize("version", VERSIONS)
def test_every_scan_names_its_relation_schema_and_alias(version: int) -> None:
    for name in EXPECTED:
        for node in nodes(plan_of(version, name)):
            if "Relation Name" in node:
                assert isinstance(node["Schema"], str), name
                assert isinstance(node["Alias"], str), name
            for key in ("Sort Key", "Presorted Key", "Group Key"):
                assert all(isinstance(t, str) for t in node.get(key, [])), name


@pytest.mark.parametrize("version", VERSIONS)
def test_partitions_are_scanned_under_their_own_alias(version: int) -> None:
    # The conditions name a partition as events_1, events_2, ...: the check
    # matches columns by the Alias field, never by the relation name.
    plan = plan_of(version, "partition_unindexed")
    scans = [n for n in nodes(plan) if "Relation Name" in n]
    assert {n["Relation Name"] for n in scans} == {"events_2025", "events_2026"}
    assert all(n["Alias"].startswith("events_") for n in scans)
    assert all(f"{n['Alias']}.kind" in n["Filter"] for n in scans)


@pytest.mark.parametrize("version", VERSIONS)
def test_temporary_tables_are_named_by_the_check_as_pg_temp(version: int) -> None:
    [issue] = replay(version, "temp_table")
    # PostgreSQL 14 prints the session's own pg_temp_<n>; the check, which
    # needs 16, gets pg_temp and looks it up with pg_my_temp_schema().
    if version >= indexes.MIN_SERVER_VERSION:
        assert issue.schema == indexes.TEMP_SCHEMA
    else:
        assert issue.schema is not None and issue.schema.startswith("pg_temp")


@pytest.mark.parametrize("version", VERSIONS)
def test_sub_plan_references_are_parsed(version: int) -> None:
    [outer] = [
        n for n in nodes(plan_of(version, "initplan")) if n.get("Alias") == "orders"
    ]
    condition = outer["Filter"]
    # $0 before PostgreSQL 17, (InitPlan 1).col1 from 17
    assert "$0" in condition or "InitPlan" in condition
    assert indexes.hint_column(condition, "orders") == "customer_id"


# ------------------------------------------------- seeded fuzzing of real plans


def _all_plans() -> list[tuple[int, str]]:
    return [(v, n) for v in VERSIONS for n in sorted(EXPECTED)]


_JUNK: list[Any] = [None, 0, 1.5, "", "x", [], {}, ["a"], {"Plans": None}, True]


def _mutate(node: Any, rng: random.Random) -> Any:
    """A copy of a plan with keys dropped, values replaced and junk nested."""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            roll = rng.random()
            if roll < 0.05:
                continue
            if roll < 0.10:
                out[key] = rng.choice(_JUNK)
            else:
                out[key] = _mutate(value, rng)
        if rng.random() < 0.05:
            out["Plans"] = [
                rng.choice(_JUNK),
                _mutate(node, rng) if rng.random() < 0.1 else {},
            ]
        return out
    if isinstance(node, list):
        return [_mutate(item, rng) for item in node if rng.random() > 0.05]
    if isinstance(node, str) and rng.random() < 0.1:
        chars = list(node)
        for _ in range(rng.randint(1, 3)):
            chars.insert(rng.randrange(len(chars) + 1), rng.choice("()'\".:$ =<>[]"))
        return "".join(chars)
    return node


def test_mutated_real_plans_never_raise() -> None:
    """Missing fields, wrong types and broken conditions degrade to fewer findings."""
    rng = random.Random(20261013)
    for version, name in _all_plans():
        leading, varchar = catalog(version, name)
        for _ in range(3):
            plan = _mutate(plan_of(version, name), rng)
            issues = find_scans(plan, "public", leading, varchar)
            assert all(isinstance(i, ScanIssue) for i in issues)
            indexes.index_names(plan)
            indexes._text_cast_tables(plan)
            for issue in issues:
                advisor.analyse(issue, frozenset())
                partial.may_serve([], issue.conditions, issue.alias)


def test_unreadable_explain_output_is_no_plan() -> None:
    rng = random.Random(5)
    raw = json.dumps(document(17, "eq_param")["plan"])
    for _ in range(300):
        cut = rng.randrange(len(raw))
        text = raw[:cut] + rng.choice(["", "]", "}", "x", "\\x00"]) + raw[cut + 1 :]
        result = indexes._read_plan(text)
        assert result is None or isinstance(result, dict)
    for junk in (None, 1, "[]", "{}", [[]], [{}], [{"Plan": []}], b"[1]"):
        assert indexes._read_plan(junk) is None


def test_sort_keys_of_real_plans_survive_mutation() -> None:
    rng = random.Random(11)
    keys = sorted(
        {
            text
            for version, name in _all_plans()
            for node in nodes(plan_of(version, name))
            for text in node.get("Sort Key", [])
        }
    )
    assert keys
    for text in keys:
        for _ in range(40):
            chars = list(text)
            position = rng.randrange(len(chars) + 1)
            chars.insert(position, rng.choice(" ()\"'.,:DESCNULLS"))
            result = advisor._sort_columns(["".join(chars)], "orders")
            assert isinstance(result, tuple)
