from __future__ import annotations

from pathlib import Path

import pytest

from pytest_queryguard import plugin

# pytester runs sessions in-process, so the outer plugin sees their queries too.
pytestmark = pytest.mark.allow_nplusone

MODELS = (Path(__file__).parent / "models.py").read_text()

RELATIONSHIP = """
from qgmodels import Order, make_sync_session


def test_loop():
    session = make_sync_session()
    for order in session.query(Order).all():
        _ = order.customer.name
"""

REPEAT = """
from sqlalchemy import create_engine, text


def test_loop():
    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        for i in range(6):
            conn.execute(text("select :i"), {"i": i}).all()
"""

BOTH = """
from sqlalchemy import text
from qgmodels import Order, make_sync_session


def test_loop():
    session = make_sync_session()
    for order in session.query(Order).all():
        _ = order.customer.name
    for i in range(6):
        session.execute(text("select :i"), {"i": i}).all()
"""

BUDGET = """
import pytest
from sqlalchemy import create_engine, text


@pytest.mark.max_queries(2)
def test_loop():
    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        for i in range(6):
            conn.execute(text("select :i"), {"i": i}).all()
"""


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makepyfile(qgmodels=MODELS)
    return pytester


def _run(
    project: pytest.Pytester, body: str, fail_on: str | None, *args: str
) -> pytest.RunResult:
    project.makepyfile(test_a=body)
    ini = "[pytest]\n"
    if fail_on is not None:
        indented = fail_on.replace("\n", "\n    ")
        ini += f"queryguard_fail_on =\n    {indented}\n"
    project.makeini(ini)
    return project.runpytest(*args)


def test_every_kind_fails_by_default(project: pytest.Pytester) -> None:
    for body in (RELATIONSHIP, REPEAT):
        _run(project, body, None).assert_outcomes(failed=1)


def test_a_kind_outside_fail_on_only_warns(project: pytest.Pytester) -> None:
    result = _run(project, REPEAT, "relationship-load")
    result.assert_outcomes(passed=1, warnings=1)
    result.stdout.fnmatch_lines(
        [
            "*QueryGuardWarning: queryguard: N+1 detected*",
            "*the same statement ran 6 times*",
        ]
    )


def test_a_kind_in_fail_on_fails(project: pytest.Pytester) -> None:
    result = _run(project, RELATIONSHIP, "relationship-load")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*Order.customer was lazy-loaded*"])


def test_the_other_kind_of_a_test_with_both(project: pytest.Pytester) -> None:
    result = _run(project, BOTH, "repeated-statement")
    result.assert_outcomes(failed=1)
    # the failing kind first, then the one that would only have warned
    result.stdout.fnmatch_lines(
        ["*the same statement ran 6 times*", "*Order.customer was lazy-loaded*"]
    )


def test_fail_on_takes_several_kinds_and_globs(project: pytest.Pytester) -> None:
    _run(
        project, RELATIONSHIP, "repeated-statement\nrelationship-load"
    ).assert_outcomes(failed=1)
    _run(project, RELATIONSHIP, "*-load").assert_outcomes(failed=1)
    _run(project, REPEAT, "*-load").assert_outcomes(passed=1, warnings=1)


def test_fail_on_skips_blank_lines_and_comments(project: pytest.Pytester) -> None:
    _run(
        project, RELATIONSHIP, "# the rest only warns\nrelationship-load\n"
    ).assert_outcomes(failed=1)


def test_warn_mode_stays_warn_mode(project: pytest.Pytester) -> None:
    result = _run(project, RELATIONSHIP, "relationship-load", "--queryguard-mode=warn")
    result.assert_outcomes(passed=1, warnings=1)


def test_off_mode_reports_nothing(project: pytest.Pytester) -> None:
    result = _run(project, RELATIONSHIP, "relationship-load", "--queryguard-mode=off")
    result.assert_outcomes(passed=1)


def test_fail_on_does_not_gate_budgets(project: pytest.Pytester) -> None:
    result = _run(project, BUDGET, "missing-index")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*expected at most 2 queries, got 6*"])
    # the repeat finding only warns by this configuration, so it is shown with
    # the budget failure instead of being a second report
    result.stdout.fnmatch_lines(["*the same statement ran 6 times*"])


def test_an_unknown_kind_is_a_usage_error(project: pytest.Pytester) -> None:
    result = _run(project, REPEAT, "n-plus-one")
    assert result.ret != 0
    result.stderr.fnmatch_lines(["*queryguard_fail_on*matches no finding kind*"])


def test_column_loads_can_be_gated_on_their_own(project: pytest.Pytester) -> None:
    body = """
from sqlalchemy import select
from sqlalchemy.orm import defer
from qgmodels import Order, make_sync_session


def test_loop():
    session = make_sync_session()
    for _ in range(2):  # 12 orders in total
        for order in session.scalars(select(Order).options(defer(Order.customer_id))):
            _ = order.customer_id
        session.expunge_all()
"""
    result = _run(project, body, "relationship-load")
    result.assert_outcomes(passed=1, warnings=1)
    result.stdout.fnmatch_lines(["*Order.customer_id was loaded on its own*"])
    _run(project, body, "column-load").assert_outcomes(failed=1)


def _with_scans(monkeypatch: pytest.MonkeyPatch) -> None:
    from pytest_queryguard.collector import QueryCollector
    from pytest_queryguard.events import ScanEvent, ScanIssue

    issue = ScanIssue("public", "orders", "Seq Scan", "filter", "(a = $1)", "a")
    real = QueryCollector

    def with_scan() -> QueryCollector:
        collector = real()
        collector.scans.append(ScanEvent(issue, "SELECT 1", None))
        return collector

    monkeypatch.setattr(plugin, "QueryCollector", with_scan)


def test_missing_index_findings_obey_fail_on(
    project: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_scans(monkeypatch)
    ok = "def test_ok():\n    pass\n"
    result = _run(project, ok, "relationship-load", "--queryguard-index-check")
    result.assert_outcomes(passed=1, warnings=1)
    result.stdout.fnmatch_lines(["*public.orders is read in full*"])
    result = _run(project, ok, "missing-index", "--queryguard-index-check")
    result.assert_outcomes(failed=1)
