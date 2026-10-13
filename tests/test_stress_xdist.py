"""A 500-test suite under pytest-xdist with four workers (stability plan S3).

Needs ``QUERYGUARD_STRESS=1`` (set by the xdist CI job): each test starts
several pytest processes over the whole generated suite.

What it pins down:

* The tests that fail are exactly the ones with a finding, whichever worker
  runs them and whatever ran on that worker before: nothing leaks between
  tests or workers.
* ``--queryguard-update-baseline`` collects the findings of every worker; the
  file is byte-identical to the one a serial run writes, and a run with it is
  clean.
* The SARIF file merged from the workers has the same results as a serial
  run, with each finding counted over all the tests that made it.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("xdist")
pytestmark = pytest.mark.skipif(
    os.environ.get("QUERYGUARD_STRESS") != "1",
    reason="set QUERYGUARD_STRESS=1 to run the 500-test stress runs",
)

MODELS = (Path(__file__).parent / "models.py").read_text()

FILES = 10
PER_FILE = 50
# In each file: test i has a relationship N+1 when i % 10 == 3 and a repeated
# statement when i % 10 == 7; the others are clean. 5 + 5 per file.
TEMPLATE = """
import pytest
from sqlalchemy import text
from qgmodels import Order, make_sync_session


def lazy_loop_{f}():
    session = make_sync_session()
    for order in session.query(Order).all():
        _ = order.customer.name
    session.close()


def repeat_{f}():
    session = make_sync_session()
    for _ in range(6):
        session.execute(text("SELECT {f} AS file_{f}")).all()
    session.close()


def clean_{f}(i):
    session = make_sync_session()
    session.query(Order).filter(Order.id == i).all()
    session.close()


@pytest.mark.parametrize("i", range({per_file}))
def test_case(i):
    if i % 10 == 3:
        lazy_loop_{f}()
    elif i % 10 == 7:
        repeat_{f}()
    else:
        clean_{f}(i)
"""

COMMON = ("-p", "no:randomly", "-p", "no:cacheprovider", "-q")


def _expected_failures() -> set[str]:
    return {
        f"test_gen_{f:02d}.py::test_case[{i}]"
        for f in range(FILES)
        for i in range(PER_FILE)
        if i % 10 in (3, 7)
    }


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makepyfile(qgmodels=MODELS)
    for f in range(FILES):
        pytester.makepyfile(
            **{f"test_gen_{f:02d}": TEMPLATE.format(f=f, per_file=PER_FILE)}
        )
    return pytester


def _failed(result: pytest.RunResult) -> set[str]:
    return set(
        re.findall(r"^FAILED (\S+?)(?: - |$)", result.stdout.str(), re.MULTILINE)
    )


def _run(project: pytest.Pytester, *args: str) -> pytest.RunResult:
    result = project.runpytest_subprocess(*COMMON, *args, timeout=600)
    assert "INTERNALERROR" not in result.stdout.str() + result.stderr.str()
    assert "QueryGuardInternalWarning" not in result.stdout.str()
    return result


def _sarif_results(path: Path) -> list[dict[str, Any]]:
    document = json.loads(path.read_text())
    results = document["runs"][0]["results"]
    return sorted(results, key=lambda r: json.dumps(r, sort_keys=True))


def test_500_tests_on_4_workers_fail_exactly_where_they_should(
    project: pytest.Pytester,
) -> None:
    expected = _expected_failures()
    for args in (("-n", "4"), ("-n", "4", "--dist", "loadfile"), ()):
        result = _run(project, *args)
        result.assert_outcomes(
            passed=FILES * PER_FILE - len(expected), failed=len(expected)
        )
        assert _failed(result) == expected, args
    # every failure names its own file's function: no finding of one test
    # was reported on another
    text = result.stdout.str()
    for f in range(FILES):
        assert text.count(f"in lazy_loop_{f}") >= 1
        assert f"AS file_{f}" in text


def test_baseline_update_and_read_with_4_workers(project: pytest.Pytester) -> None:
    update = ("--queryguard-baseline=qg.json", "--queryguard-update-baseline")
    serial = _run(project, *update)
    serial.assert_outcomes(passed=FILES * PER_FILE)
    serial_text = (project.path / "qg.json").read_bytes()
    (project.path / "qg.json").unlink()

    parallel = _run(project, "-n", "4", *update)
    parallel.assert_outcomes(passed=FILES * PER_FILE)
    parallel.stdout.fnmatch_lines(["*qg.json: 20 entries (20 added, 0 removed)*"])
    assert (project.path / "qg.json").read_bytes() == serial_text

    clean = _run(project, "-n", "4", "--queryguard-baseline=qg.json")
    clean.assert_outcomes(passed=FILES * PER_FILE)
    assert "stale" not in clean.stdout.str()

    # Drop the entries of one file: exactly its findings come back.
    document = json.loads(serial_text)
    document["findings"] = [
        entry for entry in document["findings"] if entry["file"] != "test_gen_04.py"
    ]
    (project.path / "qg.json").write_text(json.dumps(document))
    partial = _run(project, "-n", "4", "--queryguard-baseline=qg.json")
    assert _failed(partial) == {
        name for name in _expected_failures() if name.startswith("test_gen_04.py")
    }


def test_sarif_from_4_workers_matches_a_serial_run(project: pytest.Pytester) -> None:
    _run(project, "--queryguard-sarif=serial.sarif")
    _run(project, "-n", "4", "--queryguard-sarif=parallel.sarif")
    _run(project, "-n", "4", "--queryguard-sarif=again.sarif")
    serial = _sarif_results(project.path / "serial.sarif")
    assert len(serial) == 20
    assert _sarif_results(project.path / "parallel.sarif") == serial
    # deterministic, byte for byte, between two parallel runs
    assert (project.path / "parallel.sarif").read_bytes() == (
        project.path / "again.sarif"
    ).read_bytes()
    for result in serial:
        assert result["properties"]["count"] >= 5  # merged over five tests
