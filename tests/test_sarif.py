from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pytest_queryguard import baseline, sarif
from pytest_queryguard.findings import KIND_RELATIONSHIP, KIND_REPEAT, Finding

# pytester runs sessions in-process, so the outer plugin sees their queries too.
pytestmark = pytest.mark.allow_nplusone

MODELS = (Path(__file__).parent / "models.py").read_text()

LOOP = """
import pytest
from qgmodels import Customer, Order, make_sync_session


def test_loop():
    session = make_sync_session()
    for order in session.query(Order).all():
        _ = order.customer.name


def test_other_loop():
    session = make_sync_session()
    for customer in session.query(Customer).all():
        _ = customer.orders
"""


def _finding(**kwargs: Any) -> Finding:
    values: dict[str, Any] = {
        "kind": KIND_RELATIONSHIP,
        "label": "Order.customer",
        "file": "app/orders.py",
        "function": "list_orders",
        "message": "  Order.customer was lazy-loaded 3 times\n  fix: joinedload",
        "line": 12,
        "count": 3,
    }
    values.update(kwargs)
    return Finding(**values)


def _doc(*findings: Finding, level: str = "error") -> dict[str, Any]:
    store: sarif.Collected = {}
    for f in findings:
        sarif.add(store, f, level)
    return sarif.build(store, "1.2.3")[0]


# --- a structural check of the SARIF 2.1.0 requirements ---
# The OASIS schema is not vendored (its terms are not an SPDX license), so the
# properties the specification and GitHub require are checked by hand.


def check_sarif(document: dict[str, Any]) -> None:
    assert document["version"] == "2.1.0"
    assert document["$schema"].endswith("sarif-2.1.0.json")
    (run,) = document["runs"]
    driver = run["tool"]["driver"]
    assert driver["name"] and driver["version"]
    rules = driver["rules"]
    ids = [rule["id"] for rule in rules]
    assert len(ids) == len(set(ids))
    for rule in rules:
        assert rule["shortDescription"]["text"]
        assert rule["help"]["text"] and rule["help"]["markdown"]
        assert rule["defaultConfiguration"]["level"] in {
            "none",
            "note",
            "warning",
            "error",
        }
    for result in run["results"]:
        assert rules[result["ruleIndex"]]["id"] == result["ruleId"]
        assert result["level"] in {"none", "note", "warning", "error"}
        assert result["message"]["text"]
        (location,) = result["locations"]
        physical = location["physicalLocation"]
        uri = physical["artifactLocation"]["uri"]
        assert not uri.startswith(("/", "file:")) and "\\" not in uri
        assert physical["region"]["startLine"] >= 1
        assert result["partialFingerprints"]["primaryLocationLineHash"]
        assert result["partialFingerprints"][sarif.FINGERPRINT_KEY]


def test_document_shape() -> None:
    document = _doc(_finding())
    check_sarif(document)
    run = document["runs"][0]
    assert [r["id"] for r in run["tool"]["driver"]["rules"]] == [
        f"queryguard/{kind}" for kind in sarif.KINDS
    ]
    (result,) = run["results"]
    assert result["ruleId"] == "queryguard/relationship-load"
    assert (
        result["message"]["text"]
        == "Order.customer was lazy-loaded 3 times\nfix: joinedload"
    )
    location = result["locations"][0]
    assert location["physicalLocation"]["artifactLocation"] == {"uri": "app/orders.py"}
    assert location["physicalLocation"]["region"] == {"startLine": 12}
    assert location["logicalLocations"] == [{"name": "list_orders", "kind": "function"}]


def test_every_rule_has_a_fix_hint() -> None:
    for rule in _doc()["runs"][0]["tool"]["driver"]["rules"]:
        assert "Fix:" in rule["help"]["text"]
        assert "**Fix:**" in rule["help"]["markdown"]


def test_fingerprint_follows_the_identity_only() -> None:
    a = _doc(_finding(line=12, count=3))["runs"][0]["results"][0]
    b = _doc(_finding(line=99, count=50, message="other"))["runs"][0]["results"][0]
    assert a["partialFingerprints"] == b["partialFingerprints"]
    c = _doc(_finding(function="other"))["runs"][0]["results"][0]
    assert c["partialFingerprints"] != a["partialFingerprints"]
    assert len(a["partialFingerprints"][sarif.FINGERPRINT_KEY]) == 64


def test_output_is_deterministic_and_sorted() -> None:
    one = _finding(label="B.b", file="z.py")
    two = _finding(label="A.a", file="a.py")
    assert sarif.dumps(_doc(one, two)) == sarif.dumps(_doc(two, one))
    labels = [r["properties"]["label"] for r in _doc(one, two)["runs"][0]["results"]]
    assert labels == ["A.a", "B.b"]


def test_one_result_per_identity_with_counts_added() -> None:
    first = _finding(line=30, count=2)
    second = _finding(line=10, count=5, message="  earlier")
    (result,) = _doc(first, second)["runs"][0]["results"]
    assert result["properties"]["count"] == 7
    assert result["locations"][0]["physicalLocation"]["region"]["startLine"] == 10
    assert result["message"]["text"] == "earlier"


def test_error_level_wins_when_merging() -> None:
    store: sarif.Collected = {}
    sarif.add(store, _finding(), "warning")
    sarif.add(store, _finding(), "error")
    sarif.add(store, _finding(), "warning")
    assert next(iter(store.values()))[1] == "error"


@pytest.mark.parametrize(
    "file", [None, "/usr/lib/python3/site-packages/x.py", "C:\\x\\y.py", ""]
)
def test_findings_without_a_repository_location_are_left_out(file: str | None) -> None:
    store: sarif.Collected = {}
    sarif.add(store, _finding(file=file), "error")
    sarif.add(store, _finding(label="B.b"), "error")
    document, omitted = sarif.build(store, "1")
    assert omitted == 1
    assert len(document["runs"][0]["results"]) == 1


def test_a_missing_line_becomes_line_one() -> None:
    (result,) = _doc(_finding(line=None))["runs"][0]["results"]
    assert result["locations"][0]["physicalLocation"]["region"]["startLine"] == 1


def test_wire_round_trip() -> None:
    store: sarif.Collected = {}
    sarif.add(store, _finding(), "warning")
    sarif.add(
        store,
        _finding(kind=KIND_REPEAT, label="SELECT 1", file=None, function=None),
        "error",
    )
    other: sarif.Collected = {}
    sarif.merge_wire(other, json.loads(json.dumps(sarif.to_wire(store))))
    assert other == store


def test_unknown_kind_is_left_out_not_a_crash() -> None:
    _, omitted = sarif.build(
        {("nope", "x", "a.py", "f"): (_finding(kind="nope"), "error")}, "1"
    )
    assert omitted == 1


def test_backslash_paths_become_posix() -> None:
    assert sarif.repo_path("app\\models.py") == "app/models.py"


# --- the plugin ---


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makepyfile(qgmodels=MODELS, test_a=LOOP)
    return pytester


def _results(path: Path) -> list[dict[str, Any]]:
    document = json.loads(path.read_text())
    check_sarif(document)
    return document["runs"][0]["results"]  # type: ignore[no-any-return]


def test_the_run_writes_sarif(project: pytest.Pytester) -> None:
    result = project.runpytest("--queryguard-sarif=out/qg.sarif")
    result.assert_outcomes(failed=2)
    result.stdout.fnmatch_lines(["*queryguard: SARIF written*", "*qg.sarif: 2 results"])
    results = _results(project.path / "out" / "qg.sarif")
    assert [(r["properties"]["label"], r["level"]) for r in results] == [
        ("Customer.orders", "error"),
        ("Order.customer", "error"),
    ]
    first = results[1]["locations"][0]["physicalLocation"]
    assert first["artifactLocation"]["uri"] == "test_a.py"
    assert first["region"]["startLine"] == 8


def test_no_flag_writes_nothing(project: pytest.Pytester) -> None:
    project.runpytest()
    assert list(project.path.glob("*.sarif")) == []


def test_a_clean_run_writes_an_empty_valid_file(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(test_a="def test_ok():\n    pass\n")
    result = pytester.runpytest("--queryguard-sarif=qg.sarif")
    result.assert_outcomes(passed=1)
    assert _results(pytester.path / "qg.sarif") == []


def test_warn_mode_gives_warning_level(project: pytest.Pytester) -> None:
    project.runpytest("--queryguard-sarif=qg.sarif", "--queryguard-mode=warn")
    assert {r["level"] for r in _results(project.path / "qg.sarif")} == {"warning"}


def test_fail_on_decides_the_level_per_kind(project: pytest.Pytester) -> None:
    project.makeini("[pytest]\nqueryguard_fail_on = relationship-load\n")
    project.runpytest("--queryguard-sarif=qg.sarif")
    levels = {
        r["properties"]["label"]: r["level"]
        for r in _results(project.path / "qg.sarif")
    }
    assert levels == {"Customer.orders": "error", "Order.customer": "error"}
    project.makeini("[pytest]\nqueryguard_fail_on = write-loop\n")
    project.runpytest("--queryguard-sarif=qg.sarif")
    assert {r["level"] for r in _results(project.path / "qg.sarif")} == {"warning"}


def test_ignored_findings_are_not_in_the_file(project: pytest.Pytester) -> None:
    project.makeini(
        "[pytest]\nqueryguard_ignore =\n    relationship-load Order.customer\n"
    )
    project.runpytest("--queryguard-sarif=qg.sarif")
    assert [r["properties"]["label"] for r in _results(project.path / "qg.sarif")] == [
        "Customer.orders"
    ]


def test_baselined_findings_are_left_out_new_ones_stay(
    project: pytest.Pytester,
) -> None:
    project.runpytest("--queryguard-baseline=b.json", "--queryguard-update-baseline")
    path = project.path / "b.json"
    kept = [i for i in baseline.load(path) if i[1] != "Customer.orders"]
    path.write_text(baseline.dumps(kept))
    project.runpytest("--queryguard-baseline=b.json", "--queryguard-sarif=qg.sarif")
    assert [r["properties"]["label"] for r in _results(project.path / "qg.sarif")] == [
        "Customer.orders"
    ]


def test_same_file_on_two_runs(project: pytest.Pytester) -> None:
    project.runpytest("--queryguard-sarif=a.sarif")
    project.runpytest("--queryguard-sarif=b.sarif")
    assert (project.path / "a.sarif").read_text() == (
        project.path / "b.sarif"
    ).read_text()


def test_empty_path_is_a_usage_error(project: pytest.Pytester) -> None:
    result = project.runpytest("--queryguard-sarif=")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*--queryguard-sarif needs a path*"])


def test_a_write_error_is_reported_not_raised(
    project: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(path: Path, text: str) -> None:
        raise PermissionError("read-only")

    monkeypatch.setattr(sarif, "atomic_write_text", fail)
    result = project.runpytest("--queryguard-sarif=qg.sarif")
    result.assert_outcomes(failed=2)
    result.stdout.fnmatch_lines(
        ["*queryguard SARIF could not write *qg.sarif: read-only*"]
    )


def test_findings_without_a_location_are_counted_in_the_summary(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pytest_queryguard import plugin
    from pytest_queryguard.collector import QueryCollector
    from pytest_queryguard.events import ScanEvent, ScanIssue

    issue = ScanIssue("public", "orders", "Seq Scan", "filter", "(a = $1)", "a")
    real = QueryCollector

    def make() -> QueryCollector:
        collector = real()
        collector.scans.append(ScanEvent(issue, "SELECT 1", None))
        return collector

    monkeypatch.setattr(plugin, "QueryCollector", make)
    pytester.makepyfile(test_a="def test_ok():\n    pass\n")
    result = pytester.runpytest(
        "--queryguard-index-check", "--queryguard-sarif=qg.sarif"
    )
    result.stdout.fnmatch_lines(
        ["*qg.sarif: 0 results (1 left out: no location inside the rootdir)"]
    )
