"""SARIF output under a real pytest-xdist run.

Workers collect their findings and send them to the controller in
``workeroutput``; the controller merges them and writes the file once.
"""

from __future__ import annotations

import json
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


def _orders():
    for order in make_sync_session().query(Order).all():
        _ = order.customer.name


def test_orders_a():
    _orders()


def test_orders_b():
    _orders()


def test_customers():
    for customer in make_sync_session().query(Customer).all():
        _ = customer.orders
"""
WORKERS = pytest.mark.parametrize("workers", [2, 4])


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makepyfile(qgmodels=MODELS, test_loops=LOOPS)
    return pytester


def _run(project: pytest.Pytester, *args: str) -> pytest.RunResult:
    return project.runpytest_subprocess(*args, "-p", "no:cacheprovider")


@WORKERS
def test_workers_produce_the_same_file_as_a_serial_run(
    project: pytest.Pytester, workers: int
) -> None:
    _run(project, "--queryguard-sarif=serial.sarif")
    result = _run(project, "-n", str(workers), "--queryguard-sarif=parallel.sarif")
    result.assert_outcomes(passed=6, failed=3)
    result.stdout.fnmatch_lines(
        ["*queryguard: SARIF written*", "*parallel.sarif: 2 results"]
    )
    serial = (project.path / "serial.sarif").read_text()
    assert (project.path / "parallel.sarif").read_text() == serial
    results = json.loads(serial)["runs"][0]["results"]
    # two tests share one identity (same helper), so they make one result of count 6
    by_label = {r["properties"]["label"]: r["properties"]["count"] for r in results}
    assert by_label == {"Customer.orders": 3, "Order.customer": 6}
    peaks = {
        r["properties"]["label"]: r["properties"].get("peakCount") for r in results
    }
    assert peaks == {"Customer.orders": None, "Order.customer": 3}
    merged = next(r for r in results if r["properties"]["label"] == "Order.customer")
    assert "6 times in total, up to 3 times in one test" in merged["message"]["text"]


@WORKERS
def test_baseline_and_sarif_together_under_workers(
    project: pytest.Pytester, workers: int
) -> None:
    _run(project, "--queryguard-baseline=b.json", "--queryguard-update-baseline")
    result = _run(
        project,
        "-n",
        str(workers),
        "--queryguard-baseline=b.json",
        "--queryguard-sarif=qg.sarif",
    )
    result.assert_outcomes(passed=9)
    assert (
        json.loads((project.path / "qg.sarif").read_text())["runs"][0]["results"] == []
    )


@WORKERS
def test_workers_use_the_workspace_prefix(
    project: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, workers: int
) -> None:
    monkeypatch.setenv("GITHUB_WORKSPACE", str(project.path.parent))
    _run(project, "--queryguard-sarif=serial.sarif")
    _run(project, "-n", str(workers), "--queryguard-sarif=parallel.sarif")
    serial = (project.path / "serial.sarif").read_text()
    assert (project.path / "parallel.sarif").read_text() == serial
    results = json.loads(serial)["runs"][0]["results"]
    uris = {
        r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
        for r in results
    }
    assert uris == {f"{project.path.name}/test_loops.py"}


@pytest.mark.parametrize("stop", ["-x", "--maxfail=1"])
def test_a_stop_by_maxfail_writes_the_file_as_a_serial_run_does(
    project: pytest.Pytester, stop: str
) -> None:
    project.makepyfile(test_fail="def test_fail():\n    assert False\n")
    path = project.path / "out.sarif"
    path.write_text("previous")
    result = _run(project, "-n", "2", stop, "--queryguard-sarif=out.sarif")
    assert json.loads(path.read_text())["version"] == "2.1.0"
    result.stdout.no_fnmatch_line("*queryguard SARIF not written*")


def test_a_collection_error_under_x_leaves_the_file_alone(
    project: pytest.Pytester,
) -> None:
    project.makepyfile(test_broken="import no_such_module_anywhere\n")
    path = project.path / "out.sarif"
    path.write_text("previous")
    result = _run(project, "-n", "2", "-x", "--queryguard-sarif=out.sarif")
    assert result.ret == pytest.ExitCode.INTERRUPTED
    assert path.read_text() == "previous"
    result.stdout.fnmatch_lines(["*queryguard SARIF not written: the run was*"])


# A report without the tests of a module that did not collect would close
# their alerts. That holds also when the run goes on, with
# --continue-on-collection-errors or under pytest-xdist without -x.
COLLECTION_ERROR_RUNS = pytest.mark.parametrize(
    "args",
    [
        (),
        ("--continue-on-collection-errors",),
        ("-n", "2"),
        ("-n", "2", "--continue-on-collection-errors"),
    ],
    ids=["serial", "serial-continue", "xdist", "xdist-continue"],
)


@COLLECTION_ERROR_RUNS
def test_a_collection_error_leaves_the_file_alone_without_x(
    project: pytest.Pytester, args: tuple[str, ...]
) -> None:
    project.makepyfile(test_broken="import no_such_module_anywhere\n")
    path = project.path / "out.sarif"
    path.write_text("previous")
    result = _run(project, *args, "--queryguard-sarif=out.sarif")
    if args:  # the run went on: the other tests ran
        assert result.ret == pytest.ExitCode.TESTS_FAILED
        assert result.parseoutcomes()["passed"] > 0
    assert path.read_text() == "previous"
    result.stdout.fnmatch_lines(["*queryguard SARIF not written: the run was*"])
