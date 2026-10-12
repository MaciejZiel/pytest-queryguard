from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from pytest_queryguard.detectors import RelationshipFinding, RepeatFinding
from pytest_queryguard.events import CallSite, ScanEvent, ScanIssue
from pytest_queryguard.findings import (
    KIND_MISSING_INDEX,
    KIND_RELATIONSHIP,
    KIND_REPEAT,
    Finding,
)
from pytest_queryguard.messages import (
    format_findings,
    make_finding,
    make_findings,
    make_index_finding,
)

ROOT = Path("/work/project")
SITE = CallSite(
    filename="/work/project/app/api/orders.py", lineno=42, function="list_orders"
)


def test_relationship_finding_fields() -> None:
    raw = RelationshipFinding("Order.customer", 50, SITE, "SELECT 1")
    finding = make_finding(raw, cwd=ROOT)
    assert finding.kind == KIND_RELATIONSHIP
    assert finding.label == "Order.customer"
    assert finding.file == "app/api/orders.py"
    assert finding.function == "list_orders"
    assert finding.line == 42
    assert finding.count == 50
    assert finding.severity == "error"
    assert "Order.customer was lazy-loaded 50 times" in finding.message


def test_repeat_finding_label_is_the_full_normalized_statement() -> None:
    sql = "SELECT " + ", ".join(f"c{i}" for i in range(100)) + " FROM t WHERE id = ?"
    finding = make_finding(RepeatFinding(sql, 9, SITE), cwd=ROOT)
    assert finding.kind == KIND_REPEAT
    assert finding.label == sql  # the message truncates it, the identity must not


def test_identity_is_kind_label_file_function() -> None:
    finding = make_finding(RelationshipFinding("A.b", 2, SITE, None), cwd=ROOT)
    assert finding.identity == (
        KIND_RELATIONSHIP,
        "A.b",
        "app/api/orders.py",
        "list_orders",
    )


def test_identity_ignores_line_and_count() -> None:
    moved = CallSite(filename=SITE.filename, lineno=99, function=SITE.function)
    first = make_finding(RelationshipFinding("A.b", 2, SITE, None), cwd=ROOT)
    second = make_finding(RelationshipFinding("A.b", 70, moved, None), cwd=ROOT)
    assert first != second
    assert first.identity == second.identity


def test_identity_does_not_depend_on_the_checkout_directory() -> None:
    other = CallSite(
        filename="/ci/build/app/api/orders.py", lineno=42, function="list_orders"
    )
    a = make_finding(RepeatFinding("SELECT ?", 5, SITE), cwd=ROOT)
    b = make_finding(RepeatFinding("SELECT ?", 5, other), cwd=Path("/ci/build"))
    assert a.identity == b.identity


def test_file_outside_the_root_is_kept_as_reported() -> None:
    far = CallSite(filename="/elsewhere/x.py", lineno=1, function="f")
    finding = make_finding(RepeatFinding("SELECT ?", 5, far), cwd=ROOT)
    assert finding.file == "/elsewhere/x.py"


def test_unknown_call_site() -> None:
    finding = make_finding(RepeatFinding("SELECT ?", 5, None), cwd=ROOT)
    assert (finding.file, finding.function, finding.line) == (None, None, None)
    assert "<unknown location>" in finding.message


def test_finding_is_immutable_and_hashable() -> None:
    finding = make_finding(RepeatFinding("SELECT ?", 5, SITE), cwd=ROOT)
    with pytest.raises(FrozenInstanceError):
        finding.label = "x"  # type: ignore[misc]
    assert {finding: 1}[finding] == 1


def test_format_findings_takes_findings_and_detections_alike() -> None:
    raws = [RelationshipFinding("A.b", 2, SITE, None), RepeatFinding("S ?", 9, SITE)]
    findings = make_findings(raws, cwd=ROOT)
    assert [f.kind for f in findings] == [KIND_RELATIONSHIP, KIND_REPEAT]
    assert format_findings(findings, cwd=ROOT) == format_findings(raws, cwd=ROOT)


def test_index_finding() -> None:
    issue = ScanIssue(
        schema=None,
        table="orders",
        node="Seq Scan",
        kind="filter",
        condition="(customer_id = 1)",
        column="customer_id",
    )
    finding = make_index_finding(ScanEvent(issue, "SELECT 1", SITE), cwd=ROOT)
    assert finding.kind == KIND_MISSING_INDEX
    assert finding.label == "orders"
    assert finding.file == "app/api/orders.py"
    assert "CREATE INDEX ON orders (customer_id)" in finding.message


def test_index_finding_without_call_site() -> None:
    issue = ScanIssue(
        schema=None,
        table="orders",
        node="Seq Scan",
        kind="filter",
        condition="(a = 1)",
        column=None,
    )
    finding = make_index_finding(ScanEvent(issue, "SELECT 1", None), cwd=ROOT)
    assert finding.file is None and finding.function is None


def test_finding_is_a_plain_value() -> None:
    finding = Finding(kind="k", label="l", file=None, function=None, message="m")
    assert finding.severity == "error" and finding.count == 1
