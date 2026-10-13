"""The baseline under a real pytest-xdist run.

Workers read the file and filter their own findings; the controller merges
what the workers saw (``workeroutput``) and writes the file on an update.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("xdist")

MODELS = (Path(__file__).parent / "models.py").read_text()

LOOPS = """
import pytest
from qgmodels import Customer, Order, make_sync_session


@pytest.mark.parametrize("i", range(6))
def test_clean(i):
    make_sync_session().query(Order).all()


def test_orders():
    for order in make_sync_session().query(Order).all():
        _ = order.customer.name


def test_customers():
    for customer in make_sync_session().query(Customer).all():
        _ = customer.orders


def test_orders_again():
    for order in make_sync_session().query(Order).all():
        _ = order.customer.name
"""
WORKERS = pytest.mark.parametrize("workers", [2, 4])


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makepyfile(qgmodels=MODELS, test_loops=LOOPS)
    return pytester


def _run(project: pytest.Pytester, *args: str) -> pytest.RunResult:
    return project.runpytest_subprocess(*args, "-p", "no:cacheprovider")


@WORKERS
def test_update_under_workers_matches_a_serial_update(
    project: pytest.Pytester, workers: int
) -> None:
    _run(project, "--queryguard-baseline=serial.json", "--queryguard-update-baseline")
    result = _run(
        project,
        "-n",
        str(workers),
        "--queryguard-baseline=parallel.json",
        "--queryguard-update-baseline",
    )
    result.assert_outcomes(passed=9)
    result.stdout.fnmatch_lines(["*queryguard: baseline updated*"])
    serial = (project.path / "serial.json").read_text()
    assert (project.path / "parallel.json").read_text() == serial
    assert serial.count('"kind"') == 3  # the two tests of one label differ by function


@WORKERS
def test_workers_apply_the_baseline_and_report_new_findings(
    project: pytest.Pytester, workers: int
) -> None:
    _run(project, "--queryguard-baseline=qg.json", "--queryguard-update-baseline")
    clean = _run(project, "-n", str(workers), "--queryguard-baseline=qg.json")
    clean.assert_outcomes(passed=9)
    clean.stdout.no_fnmatch_line("*stale baseline*")
    # drop the entry for Customer.orders: only that test fails
    path = project.path / "qg.json"
    text = path.read_text()
    assert "Customer.orders" in text
    from pytest_queryguard import baseline

    kept = [i for i in baseline.parse(text, path) if i[1] != "Customer.orders"]
    path.write_text(baseline.dumps(kept))
    result = _run(project, "-n", str(workers), "--queryguard-baseline=qg.json")
    result.assert_outcomes(passed=8, failed=1)
    result.stdout.fnmatch_lines(["*Customer.orders was lazy-loaded*"])
    result.stdout.no_fnmatch_line("*Order.customer was lazy-loaded*")


@WORKERS
def test_stale_entries_are_found_from_what_the_workers_saw(
    project: pytest.Pytester, workers: int
) -> None:
    from pytest_queryguard import baseline

    _run(project, "--queryguard-baseline=qg.json", "--queryguard-update-baseline")
    path = project.path / "qg.json"
    path.write_text(
        baseline.dumps(
            [
                *baseline.parse(path.read_text(), path),
                ("write-loop", "gone", "x.py", "f"),
            ]
        )
    )
    result = _run(project, "-n", str(workers), "--queryguard-baseline=qg.json")
    result.assert_outcomes(passed=9)
    result.stdout.fnmatch_lines(
        [
            "*queryguard: stale baseline entries*",
            "1 baseline entry in *",
            "  write-loop gone (x.py, f)",
        ]
    )


def test_a_worker_that_crashes_does_not_rewrite_the_file(
    project: pytest.Pytester,
) -> None:
    project.makepyfile(
        test_crash="""
        import os

        def test_crash():
            os._exit(1)
        """
    )
    path = project.path / "qg.json"
    path.write_text('{"version": 1, "findings": []}\n')
    result = _run(
        project,
        "-n",
        "2",
        "--queryguard-baseline=qg.json",
        "--queryguard-update-baseline",
    )
    assert path.read_text() == '{"version": 1, "findings": []}\n'
    result.stdout.fnmatch_lines(["*queryguard baseline not updated*"])


@pytest.mark.parametrize("stop", ["-x", "--maxfail=1"])
def test_a_stop_by_maxfail_gives_the_serial_reason(
    project: pytest.Pytester, stop: str
) -> None:
    project.makepyfile(
        test_fail="""
        def test_fail():
            assert False
        """
    )
    path = project.path / "qg.json"
    path.write_text('{"version": 1, "findings": []}\n')
    result = _run(
        project,
        "-n",
        "2",
        stop,
        "--queryguard-baseline=qg.json",
        "--queryguard-update-baseline",
    )
    assert path.read_text() == '{"version": 1, "findings": []}\n'
    result.stdout.fnmatch_lines(
        ["*queryguard baseline not updated: the run stopped early*"]
    )
    result.stdout.no_fnmatch_line("*Ctrl-C*")
