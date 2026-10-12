"""The plan parsing on real EXPLAIN output from PostgreSQL 14 to 18.

tests/fixtures/plans/pg<major>/<name>.json was captured by
scripts/capture_plans.py from a live server: EXPLAIN (GENERIC_PLAN, VERBOSE,
FORMAT JSON) on 16 to 18, and the same generic plan of a prepared statement on
14 and 15. These tests need no database.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from pytest_queryguard import indexes, predicates
from pytest_queryguard.indexes import find_scans

PLANS = Path(__file__).parent / "fixtures" / "plans"
VERSIONS = [14, 15, 16, 17, 18]
CONDITION_KEYS = ("Filter", "Index Cond", "Recheck Cond", "Join Filter", "Hash Cond")

# What the fixture tables look like to the catalog lookups of the check.
VARCHAR = {
    ("public", "orders"): frozenset({"status"}),
    ("public", "customers"): frozenset({"email", "name"}),
}
LEADING = {("public", "order_items_order_product"): "order_id"}

# name -> [(table, node, kind, column, expression)], the same on every version
# unless EXPECTED_BY_VERSION says otherwise.
SEQ = "Seq Scan"
EXPECTED: dict[str, list[tuple[str, str, str, str | None, bool]]] = {
    "eq_param": [("orders", SEQ, "filter", "customer_id", False)],
    "eq_any_array": [("orders", SEQ, "filter", "customer_id", False)],
    "eq_in_list": [("orders", SEQ, "filter", "customer_id", False)],
    "varchar_cast": [("orders", SEQ, "filter", "status", False)],
    "char_cast": [("orders", SEQ, "filter", "kind", False)],
    "literal_constant": [("orders", SEQ, "filter", "status", False)],
    "range_two_sided": [("orders", SEQ, "filter", "total", False)],
    "range_one_sided": [("orders", SEQ, "filter", "total", False)],
    "eq_and_range": [("orders", SEQ, "filter", None, False)],
    "two_equalities": [("orders", SEQ, "filter", None, False)],
    "join_orders_customers": [("orders", SEQ, "filter", "customer_id", False)],
    "left_join_alias": [
        ("customers", "Index Scan using customers_pkey", "filter", "email", False)
    ],
    "function_lower": [("customers", SEQ, "filter", None, True)],
    "cast_date": [("orders", SEQ, "filter", None, True)],
    "coalesce": [("orders", SEQ, "filter", None, True)],
    "like": [("customers", SEQ, "filter", None, False)],
    "is_null": [("orders", SEQ, "filter", None, False)],
    "not_equal": [("orders", SEQ, "filter", None, False)],
    "not_condition": [("orders", SEQ, "filter", None, False)],
    "array_any": [("orders", SEQ, "filter", None, False)],
    "json_arrow": [("orders", SEQ, "filter", None, False)],
    "or_two_columns": [("orders", SEQ, "filter", None, False)],
    "trailing_index_column": [
        (
            "order_items",
            "Bitmap Index Scan using order_items_order_product",
            "index condition",
            "product_id",
            False,
        )
    ],
    "leading_index_column": [],
    "whole_index_order_limit": [
        ("orders", "Index Scan using orders_pkey", "filter", "customer_id", False)
    ],
    "order_by_no_filter": [],
    "quoted_identifiers": [("Mixed Case", SEQ, "filter", '"customerId"', False)],
    "quoted_alias": [("Mixed Case", SEQ, "filter", '"customerId"', False)],
    "initplan": [
        ("orders", SEQ, "filter", "customer_id", False),
        ("customers", SEQ, "filter", "email", False),
    ],
    "in_subquery": [("orders", SEQ, "filter", "total", False)],
    "correlated_subplan": [("orders", SEQ, "filter", "customer_id", False)],
    "partial_index_match": [],
    "partial_index_other_column": [("archive", SEQ, "filter", "owner_id", False)],
    "cte": [("orders", SEQ, "filter", "customer_id", False)],
    "group_by_filter": [("orders", SEQ, "filter", "customer_id", False)],
    "order_by_with_equality": [("orders", SEQ, "filter", "customer_id", False)],
}

# PostgreSQL 18 skips the leading column of the index, so the check does not
# judge a condition on the trailing one.
EXPECTED_BY_VERSION: dict[tuple[str, int], list[Any]] = {
    ("trailing_index_column", 18): [],
}


def load(version: int, name: str) -> tuple[str, dict[str, Any]]:
    document = json.loads((PLANS / f"pg{version}" / f"{name}.json").read_text())
    return document["query"], document["plan"][0]["Plan"]


def nodes(plan: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield plan
    for child in plan.get("Plans", []):
        yield from nodes(child)


def test_every_version_has_every_fixture() -> None:
    names = {p.stem for p in (PLANS / "pg17").glob("*.json")}
    assert names == set(EXPECTED)
    for version in VERSIONS:
        assert {p.stem for p in (PLANS / f"pg{version}").glob("*.json")} == names


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_find_scans_on_real_plans(version: int, name: str) -> None:
    _, plan = load(version, name)
    leading = LEADING if version < indexes.SKIP_SCAN_VERSION else {}
    issues = find_scans(plan, "public", leading, VARCHAR)
    found = [(i.table, i.node, i.kind, i.column, i.expression) for i in issues]
    assert found == EXPECTED_BY_VERSION.get((name, version), EXPECTED[name])


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_conditions_of_real_plans_are_parsed_and_agree_with_text_matching(
    version: int, name: str
) -> None:
    _, plan = load(version, name)
    for node in nodes(plan):
        alias = node.get("Alias", "")
        for key in CONDITION_KEYS:
            condition = node.get(key)
            if condition is None:
                continue
            assert predicates.parse(condition) is not None, condition
            for varchar in (frozenset(), frozenset({"status", "name", "email"})):
                assert indexes._comparisons(condition, alias, varchar) == (
                    indexes._regex_comparisons(condition, alias, varchar)
                ), condition
                assert indexes._needs_expression(condition, alias, varchar) == (
                    indexes._regex_needs_expression(condition, alias, varchar)
                ), condition


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_plans_of_all_versions_agree_on_the_findings(name: str) -> None:
    results = set()
    for version in VERSIONS:
        _, plan = load(version, name)
        leading = LEADING if version < indexes.SKIP_SCAN_VERSION else {}
        issues = find_scans(plan, "public", leading, VARCHAR)
        results.add(tuple((i.table, i.column, i.kind) for i in issues))
    assert len(results) == (2 if name == "trailing_index_column" else 1)


@pytest.mark.parametrize("version", VERSIONS)
def test_structured_fields_name_what_the_check_reads(version: int) -> None:
    # Relation, schema, alias and index come as fields; the conditions are text.
    _, plan = load(version, "left_join_alias")
    scans = [n for n in nodes(plan) if "Relation Name" in n]
    assert {(n["Relation Name"], n["Schema"], n["Alias"]) for n in scans} == {
        ("customers", "public", "customers"),
        ("orders", "public", "o2"),
    }
    assert all(isinstance(n.get("Output", []), list) for n in scans)
    conditions = [n[k] for n in nodes(plan) for k in CONDITION_KEYS if k in n]
    assert conditions and all(isinstance(c, str) for c in conditions)


@pytest.mark.parametrize("version", VERSIONS)
def test_sub_plan_references_are_parsed(version: int) -> None:
    _, plan = load(version, "initplan")
    [outer] = [n for n in nodes(plan) if n.get("Alias") == "orders"]
    condition = outer["Filter"]
    # $0 before PostgreSQL 17, (InitPlan 1).col1 from 17
    assert "$0" in condition or "InitPlan" in condition
    assert indexes.hint_column(condition, "orders") == "customer_id"
