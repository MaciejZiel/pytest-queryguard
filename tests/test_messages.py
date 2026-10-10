from __future__ import annotations

from pathlib import Path

from pytest_queryguard.detectors import Finding, RelationshipFinding, RepeatFinding
from pytest_queryguard.events import CallSite, StatementEvent
from pytest_queryguard.messages import format_budget, format_findings

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


def test_repeat_message() -> None:
    site = CallSite("/work/project/app/services/orders.py", 18, "fetch_customers")
    sql = "SELECT customer.id, customer.name FROM customer WHERE customer.id = ?"
    assert format_findings([RepeatFinding(sql, 50, site)], cwd=CWD).splitlines() == [
        "queryguard: N+1 detected",
        "",
        "  the same statement ran 50 times with different parameters",
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
