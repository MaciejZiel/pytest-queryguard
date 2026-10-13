"""The plugin next to other pytest plugins and run options (stability plan S3).

pytest-timeout is in the dev group. pytest-randomly is in the "plugins" group,
which only the "plugins" CI job installs; its tests skip without it.
pytest-rerunfailures and pytest-repeat are MPL-2.0, which this repository may
not depend on: a rerun is emulated here with pytest's own runtestprotocol(),
the call those plugins make, and a repeat is a parametrized test.
"""

from __future__ import annotations

import json
import re
import signal
import sys
from pathlib import Path

import pytest

from pytest_queryguard import baseline

# pytester runs sessions in-process, so the outer plugin sees their queries too.
pytestmark = pytest.mark.allow_nplusone

MODELS = (Path(__file__).parent / "models.py").read_text()

SUITE = """
import pytest
from qgmodels import Customer, Order, make_sync_session


@pytest.fixture
def session():
    session = make_sync_session()  # fixtures are not measured
    yield session
    session.close()


def lazy_loop(session):
    for order in session.query(Order).all():
        _ = order.customer.name


def test_a_clean(session):
    session.get(Customer, 1)


def test_b_nplusone(session):
    lazy_loop(session)


@pytest.mark.max_queries(3)
def test_c_budget(session):
    # the second get(1) comes from the identity map: 3 queries
    found = [session.get(Customer, i) for i in (1, 2, 3, 1)]
    assert found[0] is found[3]


def test_d_nplusone_again(session):
    lazy_loop(session)


def test_e_user_failure():
    assert False, "the user's own failure"
"""

EXPECTED_FAILED = {
    "test_suite.py::test_b_nplusone",
    "test_suite.py::test_d_nplusone_again",
    "test_suite.py::test_e_user_failure",
}


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makepyfile(qgmodels=MODELS)
    pytester.makepyfile(test_suite=SUITE)
    return pytester


def _failed(result: pytest.RunResult) -> set[str]:
    return set(
        re.findall(r"^FAILED (\S+?)(?: - |$)", result.stdout.str(), re.MULTILINE)
    )


def _clean(result: pytest.RunResult) -> None:
    output = result.stdout.str() + result.stderr.str()
    assert "INTERNALERROR" not in output, output[-3000:]
    assert "QueryGuardInternalWarning" not in output, output[-3000:]
    assert "measurement stopped" not in output, output[-3000:]


# ------------------------------------------------------------ pytest-timeout

needs_alarm = pytest.mark.skipif(
    not hasattr(signal, "SIGALRM"), reason="pytest-timeout's signal method"
)

SLOW_STEP = """
import time

import pytest
from pytest_queryguard import {module}

_original = {module}.{attr}
_slowed = []


def slow(*args, **kwargs):
    if not _slowed:
        _slowed.append(True)
        time.sleep(30)
    return _original(*args, **kwargs)


{module}.{attr} = slow


def pytest_unconfigure(config):
    {module}.{attr} = _original
"""


@needs_alarm
@pytest.mark.parametrize(
    ("module", "attr"),
    [
        ("listeners", "find_callsite"),  # inside SQLAlchemy's before_cursor_execute
        ("plugin", "detect"),  # in the analysis after the test body
    ],
)
def test_a_timeout_inside_the_plugin_fails_only_that_test(
    project: pytest.Pytester, module: str, attr: str
) -> None:
    pytest.importorskip("pytest_timeout")
    project.makeconftest(SLOW_STEP.format(module=module, attr=attr))
    result = project.runpytest_subprocess(
        "-p", "no:randomly", "--timeout=2", "-o", "timeout_method=signal"
    )
    _clean(result)  # the timeout is not taken for a bug of the plugin
    failed = _failed(result)
    timed_out = re.findall(
        r"^FAILED (\S+) - Failed: Timeout", result.stdout.str(), re.MULTILINE
    )
    assert len(timed_out) == 1
    # Every other test is measured as usual afterwards.
    assert failed - set(timed_out) == EXPECTED_FAILED - set(timed_out)
    assert "queryguard: N+1 detected" in result.stdout.str()


@needs_alarm
def test_a_timeout_in_the_users_query_keeps_the_next_test_measured(
    project: pytest.Pytester,
) -> None:
    pytest.importorskip("pytest_timeout")
    project.makepyfile(
        test_0_slow="""
        import time
        from sqlalchemy import create_engine, event, text


        def test_slow_statement():
            engine = create_engine("sqlite://")
            with engine.connect() as conn:
                conn.connection.dbapi_connection.create_function(
                    "pause", 1, lambda s: time.sleep(s) or 1
                )
                conn.execute(text("SELECT pause(30)"))
        """
    )
    result = project.runpytest_subprocess(
        "-p", "no:randomly", "--timeout=2", "-o", "timeout_method=signal"
    )
    _clean(result)
    assert _failed(result) == EXPECTED_FAILED | {"test_0_slow.py::test_slow_statement"}


# ------------------------------------------------------- run options of pytest


def test_exitfirst_stops_without_errors_and_keeps_the_baseline(
    project: pytest.Pytester,
) -> None:
    (project.path / "qg.json").write_text(baseline.dumps([]))
    result = project.runpytest(
        "-p",
        "no:randomly",
        "-x",
        "--queryguard-report",
        "--queryguard-sarif=out.sarif",
        "--queryguard-baseline=qg.json",
        "--queryguard-update-baseline",
    )
    _clean(result)
    # an update run reports no findings, so the user's failure stops it
    assert _failed(result) == {"test_suite.py::test_e_user_failure"}
    result.stdout.fnmatch_lines(["*baseline not updated: the run stopped early*"])
    assert baseline.load(project.path / "qg.json") == frozenset()


def test_last_failed_reruns_the_same_findings(project: pytest.Pytester) -> None:
    first = project.runpytest("-p", "no:randomly")
    assert _failed(first) == EXPECTED_FAILED
    again = project.runpytest("-p", "no:randomly", "--lf")
    _clean(again)
    assert _failed(again) == EXPECTED_FAILED
    again.assert_outcomes(failed=3)


def test_runs_without_the_cache_provider(project: pytest.Pytester) -> None:
    result = project.runpytest(
        "-p",
        "no:randomly",
        "-p",
        "no:cacheprovider",
        "--queryguard-baseline=qg.json",
        "--queryguard-update-baseline",
    )
    _clean(result)
    # both N+1 tests run the same loop: one entry
    assert len(baseline.load(project.path / "qg.json")) == 1
    result = project.runpytest(
        "-p", "no:randomly", "-p", "no:cacheprovider", "--queryguard-baseline=qg.json"
    )
    assert _failed(result) == {"test_suite.py::test_e_user_failure"}


def test_pdb_on_a_finding_does_not_hang_or_break(project: pytest.Pytester) -> None:
    # Each failure opens the debugger; "c" continues the run.
    result = project.run(
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "no:randomly",
        "--pdb",
        stdin=b"c\n" * 5,
        timeout=120,
    )
    _clean(result)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    out = result.stdout.str()
    assert out.count("entering PDB") == 3
    assert "queryguard: N+1 detected" in out


def test_a_breakpoint_inside_a_measured_test_counts_the_debugger_queries(
    project: pytest.Pytester,
) -> None:
    # Statements typed at the prompt run inside the test body, so they count:
    # here they push the test over its budget of 1.
    project.makepyfile(
        test_pdb="""
        import pytest
        from qgmodels import Customer, make_sync_session


        @pytest.fixture
        def session():
            return make_sync_session()


        @pytest.mark.max_queries(1)
        def test_debugged(session):
            session.get(Customer, 1)
            breakpoint()
        """
    )
    stdin = b"session.get(Customer, 2)\nsession.get(Customer, 3)\nc\n"
    result = project.run(
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "no:randomly",
        "test_pdb.py",
        stdin=stdin,
        timeout=120,
    )
    _clean(result)
    result.stdout.fnmatch_lines(["*expected at most 1 queries, got 3*"])


# ------------------------------------------------- reruns (rerunfailures-like)

RERUN_CONFTEST = """
import pytest
from _pytest.runner import runtestprotocol

ATTEMPTS = {{}}


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    # What pytest-rerunfailures does: run the whole protocol again after a
    # failure, reporting only the last attempt.
    item.ihook.pytest_runtest_logstart(nodeid=item.nodeid, location=item.location)
    for attempt in range({reruns} + 1):
        ATTEMPTS[item.nodeid] = attempt
        reports = runtestprotocol(item, nextitem=nextitem, log=False)
        if all(not r.failed for r in reports) or attempt == {reruns}:
            break
    for report in reports:
        item.ihook.pytest_runtest_logreport(report=report)
    item.ihook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)
    return True


@pytest.fixture
def attempt(request):
    return ATTEMPTS[request.node.nodeid]
"""

FLAKY = """
import pytest
from qgmodels import Customer, Order, make_sync_session


@pytest.fixture
def session():
    session = make_sync_session()
    yield session
    session.close()


@pytest.mark.max_queries(2)
def test_flaky_with_budget(session, attempt):
    session.get(Customer, 1)
    session.get(Customer, 2)
    assert attempt == 1, "fails on the first attempt only"


def test_flaky_nplusone(session, attempt):
    if attempt == 0:
        for order in session.query(Order).all():
            _ = order.customer.name


def test_always_nplusone(session):
    for order in session.query(Order).all():
        _ = order.customer.name
"""


def test_a_rerun_is_measured_on_its_own(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(qgmodels=MODELS, test_flaky=FLAKY)
    pytester.makeconftest(RERUN_CONFTEST.format(reruns=1))
    result = pytester.runpytest(
        "-p",
        "no:randomly",
        "--queryguard-sarif=out.sarif",
        "--queryguard-baseline=qg.json",
        "--queryguard-update-baseline",
    )
    _clean(result)
    # the budget of the second attempt does not include the first one's queries
    result.assert_outcomes(passed=3)
    entries = baseline.load(pytester.path / "qg.json")
    # A finding of a failed attempt that a rerun fixed is still recorded:
    # the code under test did run that N+1.
    assert {(kind, function) for kind, _, _, function in entries} == {
        ("relationship-load", "test_flaky_nplusone"),
        ("relationship-load", "test_always_nplusone"),
    }

    reported = pytester.runpytest("-p", "no:randomly", "--queryguard-sarif=out.sarif")
    assert _failed(reported) == {"test_flaky.py::test_always_nplusone"}
    document = json.loads((pytester.path / "out.sarif").read_text())
    results = document["runs"][0]["results"]
    # The failed first attempt of test_flaky_nplusone is in the SARIF file too,
    # like in the baseline above. Each attempt is measured on its own: 3 lazy
    # loads for the flaky test's one failing attempt, 3 + 3 for the two
    # attempts of test_always_nplusone.
    assert sorted(r["properties"]["count"] for r in results) == [3, 6]


# ------------------------------------------------------------ pytest-randomly


@pytest.mark.parametrize("seed", ["1", "2", "3"])
def test_random_order_gives_the_same_findings_and_baseline(
    project: pytest.Pytester, seed: str
) -> None:
    pytest.importorskip("pytest_randomly")
    ordered = project.runpytest(
        "-p",
        "no:randomly",
        "--queryguard-baseline=ordered.json",
        "--queryguard-update-baseline",
    )
    shuffled = project.runpytest(
        "-p",
        "randomly",
        f"--randomly-seed={seed}",
        "--queryguard-baseline=shuffled.json",
        "--queryguard-update-baseline",
    )
    _clean(shuffled)
    assert _failed(ordered) == _failed(shuffled)
    assert (project.path / "ordered.json").read_bytes() == (
        project.path / "shuffled.json"
    ).read_bytes()
    reported = project.runpytest("-p", "randomly", f"--randomly-seed={seed}")
    _clean(reported)
    assert _failed(reported) == EXPECTED_FAILED
