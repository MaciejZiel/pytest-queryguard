from __future__ import annotations

from pathlib import Path

from pytest_queryguard.detectors import Finding, RelationshipFinding, RepeatFinding
from pytest_queryguard.events import CallSite, ScanEvent, ScanIssue, StatementEvent
from pytest_queryguard.messages import (
    format_budget,
    format_findings,
    format_index_findings,
)

CWD = Path("/work/project")


def test_relationship_message_has_fix_hint() -> None:
    site = CallSite("/work/project/app/api/orders.py", 42, "list_orders")
    finding = RelationshipFinding("Order.customer", 50, site, None)
    assert format_findings([finding], cwd=CWD).splitlines() == [
        "queryguard: N+1 detected",
        "",
        "  Order.customer was lazy-loaded 50 times",
        "  first at app/api/orders.py:42 in list_orders",
        "  fix: load it with the parent query, e.g.",
        "       select(Order).options(selectinload(Order.customer))",
    ]


def test_relationship_message_shows_sample_sql_truncated() -> None:
    site = CallSite("/work/project/app/api/orders.py", 42, "list_orders")
    sql = "SELECT customer.id\nFROM customer\nWHERE customer.id = ?"
    finding = RelationshipFinding("Order.customer", 50, site, sql)
    lines = format_findings([finding], cwd=CWD).splitlines()
    assert lines[3:5] == [
        "  first at app/api/orders.py:42 in list_orders",
        "  sql: SELECT customer.id FROM customer WHERE customer.id = ?",
    ]
    long_sql = "SELECT " + "x" * 300
    long = RelationshipFinding("Order.customer", 50, site, long_sql)
    assert f"  sql: {long_sql[:200]}..." in format_findings([long]).splitlines()


def test_repeat_message() -> None:
    site = CallSite("/work/project/app/services/orders.py", 18, "fetch_customers")
    sql = "SELECT customer.id, customer.name FROM customer WHERE customer.id = ?"
    assert format_findings([RepeatFinding(sql, 50, site)], cwd=CWD).splitlines() == [
        "queryguard: N+1 detected",
        "",
        "  the same statement ran 50 times",
        "  first at app/services/orders.py:18 in fetch_customers",
        f"  {sql}",
        "  fix: fetch all rows in one query (WHERE id IN (...)) instead of in a loop",
    ]


def test_budget_message_lists_statements() -> None:
    site = CallSite("/work/project/app/a.py", 7, "run")
    long_sql = "SELECT " + "x" * 300
    statements = [
        StatementEvent("SELECT 1", site, None),
        StatementEvent(long_sql, None, None),
    ]
    lines = format_budget(1, statements, cwd=CWD).splitlines()
    assert lines[0] == "queryguard: expected at most 1 queries, got 2"
    assert "  1. SELECT 1  (app/a.py:7 in run)" in lines
    assert f"  2. {long_sql[:200]}..." in lines


def test_repeat_message_truncates_long_sql() -> None:
    long_sql = "SELECT " + "x" * 300
    lines = format_findings([RepeatFinding(long_sql, 9, None)]).splitlines()
    assert f"  {long_sql[:200]}..." in lines
    assert all(len(line) < 220 for line in lines)


def test_missing_callsite_prints_unknown_location() -> None:
    finding = RelationshipFinding("Order.customer", 3, None, None)
    out = format_findings([finding], cwd=CWD)
    assert "  first at <unknown location>" in out.splitlines()


def test_path_outside_cwd_stays_absolute_and_blocks_are_separated() -> None:
    site = CallSite("/elsewhere/lib.py", 1, "f")
    findings: list[Finding] = [
        RelationshipFinding("A.b", 2, site, None),
        RepeatFinding("SELECT ?", 9, site),
    ]
    out = format_findings(findings, cwd=CWD)
    assert "  first at /elsewhere/lib.py:1 in f" in out
    assert out.count("queryguard: N+1 detected") == 1
    assert "\n\n  the same statement" in out


def _scan(
    issue: ScanIssue, sql: str = "SELECT 1", site: CallSite | None = None
) -> ScanEvent:
    return ScanEvent(issue, sql, site)


def test_index_message_for_a_filter_with_hint() -> None:
    site = CallSite("/work/project/app/orders.py", 12, "orders_for")
    sql = "SELECT orders.id\nFROM orders\nWHERE orders.customer_id = %s::INTEGER"
    issue = ScanIssue(
        "public",
        "orders",
        "Seq Scan",
        "filter",
        "(orders.customer_id = $1)",
        "customer_id",
    )
    assert format_index_findings([_scan(issue, sql, site)], cwd=CWD).splitlines() == [
        "queryguard: missing index",
        "",
        "  orders is read in full: Seq Scan with filter (orders.customer_id = $1)",
        "  first at app/orders.py:12 in orders_for",
        "  sql: SELECT orders.id FROM orders WHERE orders.customer_id = %s::INTEGER",
        "  fix: CREATE INDEX ON orders (customer_id)",
        "",
        "  a sequential scan is fine on a small lookup table:",
        "  list it in queryguard_index_ignore_tables",
    ]


def test_index_message_for_a_join_without_hint_in_another_schema() -> None:
    issue = ScanIssue(
        "Sales", "order", "Seq Scan", "join", "(lower(o.code) = c.code)", None
    )
    lines = format_index_findings([_scan(issue)], cwd=CWD).splitlines()
    assert lines[2:6] == [
        (
            '  "Sales"."order" is read in full: Seq Scan for join condition '
            "(lower(o.code) = c.code)"
        ),
        "  first at <unknown location>",
        "  sql: SELECT 1",
        "  fix: add an index that matches this condition",
    ]


def test_index_message_names_the_index_and_quotes_the_hint() -> None:
    issue = ScanIssue(
        "public",
        "Order",
        "Index Scan using Order_pkey",
        "filter",
        '("Order"."customerId" = $1)',
        '"customerId"',
    )
    lines = format_index_findings([_scan(issue)], cwd=CWD).splitlines()
    assert lines[2] == (
        '  "Order" is read in full: Index Scan using Order_pkey with filter '
        '("Order"."customerId" = $1)'
    )
    assert lines[5] == '  fix: CREATE INDEX ON "Order" ("customerId")'


def test_index_message_separates_blocks_and_truncates() -> None:
    long_condition = "(t.a = $1)" + " AND (t.b = $2)" * 30
    issues = [
        ScanIssue("public", "a", "Seq Scan", "filter", "(a.x = $1)", "x"),
        ScanIssue("public", "b", "Seq Scan", "filter", long_condition, None),
    ]
    out = format_index_findings([_scan(i, "SELECT " + "y" * 300) for i in issues])
    assert out.count("queryguard: missing index") == 1
    assert out.count("is read in full") == 2
    assert out.count("small lookup table") == 1
    assert all(len(line) < 260 for line in out.splitlines())
