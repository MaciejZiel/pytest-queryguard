"""``--queryguard-update-baseline`` on a run that did not cover the whole suite.

Only the entries of tests that ran are replaced; the others stay.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pytest_queryguard import baseline

# pytester runs sessions in-process, so the outer plugin sees their queries too.
pytestmark = pytest.mark.allow_nplusone

MODELS = (Path(__file__).parent / "models.py").read_text()

ORDER_LOOP = """
def {name}():
    session = make_sync_session()
    for order in session.query(Order).all():
        _ = order.customer.name
"""
CUSTOMER_LOOP = """
def {name}():
    session = make_sync_session()
    for customer in session.query(Customer).all():
        _ = customer.orders
"""
HEADER = "import pytest\nfrom qgmodels import Customer, Order, make_sync_session\n"
TEST_A = (
    HEADER
    + ORDER_LOOP.format(name="test_a1")
    + CUSTOMER_LOOP.format(name="test_a2").replace(
        "def test_a2", "@pytest.mark.slow\ndef test_a2"
    )
)
TEST_B = HEADER + ORDER_LOOP.format(name="test_b1")

A1 = ("relationship-load", "Order.customer", "test_a.py", "test_a1")
A2 = ("relationship-load", "Customer.orders", "test_a.py", "test_a2")
B1 = ("relationship-load", "Order.customer", "test_b.py", "test_b1")
ALL = {A1, A2, B1}
UPDATE = ("--queryguard-baseline=qg.json", "--queryguard-update-baseline")


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makepyfile(qgmodels=MODELS, test_a=TEST_A, test_b=TEST_B)
    pytester.makeini("[pytest]\nmarkers = slow\n")
    return pytester


def _entries(project: pytest.Pytester) -> set[baseline.Identity]:
    path = project.path / "qg.json"
    return set(baseline.parse(path.read_text(), path))


def _seed(project: pytest.Pytester) -> None:
    project.runpytest(*UPDATE).assert_outcomes(passed=3)
    assert _entries(project) == ALL


def test_a_full_run_still_removes_stale_entries(project: pytest.Pytester) -> None:
    _seed(project)
    project.makepyfile(test_b=HEADER + "def test_b1():\n    pass\n")
    result = project.runpytest(*UPDATE)
    result.assert_outcomes(passed=3)
    assert _entries(project) == {A1, A2}
    result.stdout.fnmatch_lines(["*2 entries (0 added, 1 removed)"])
    result.stdout.no_fnmatch_line("*kept because*")


def test_k_subset_keeps_the_entries_of_tests_that_did_not_run(
    project: pytest.Pytester,
) -> None:
    _seed(project)
    result = project.runpytest("-k", "test_a1", *UPDATE)
    result.assert_outcomes(passed=1)
    assert _entries(project) == ALL
    result.stdout.fnmatch_lines(
        ["*3 entries (0 added, 0 removed)", "*2 entries kept because their tests*"]
    )


def test_marker_subset_keeps_the_others(project: pytest.Pytester) -> None:
    _seed(project)
    project.runpytest("-m", "slow", *UPDATE).assert_outcomes(passed=1)
    assert _entries(project) == ALL


def test_deselect_keeps_the_others(project: pytest.Pytester) -> None:
    _seed(project)
    project.runpytest("--deselect", "test_b.py::test_b1", *UPDATE)
    assert _entries(project) == ALL


def test_a_test_that_ran_and_got_fixed_is_removed_in_a_subset(
    project: pytest.Pytester,
) -> None:
    _seed(project)
    project.makepyfile(test_a=TEST_A.replace("customer.orders", "customer.id"))
    result = project.runpytest("-k", "test_a2", *UPDATE)
    result.assert_outcomes(passed=1)
    assert _entries(project) == {A1, B1}
    result.stdout.fnmatch_lines(
        ["*2 entries (0 added, 1 removed)", "*2 entries kept because their tests*"]
    )


def test_a_new_finding_is_added_in_a_subset(project: pytest.Pytester) -> None:
    project.runpytest("-k", "test_b1", *UPDATE).assert_outcomes(passed=1)
    assert _entries(project) == {B1}
    project.runpytest("-k", "test_a1", *UPDATE).assert_outcomes(passed=1)
    assert _entries(project) == {A1, B1}


def test_last_failed_keeps_the_others(project: pytest.Pytester) -> None:
    project.makepyfile(test_c="def test_c():\n    assert False\n")
    project.runpytest(*UPDATE).assert_outcomes(passed=3, failed=1)
    assert _entries(project) == ALL
    project.makepyfile(test_c="def test_c():\n    assert True\n")
    result = project.runpytest("--lf", *UPDATE)
    result.assert_outcomes(passed=1)
    assert _entries(project) == ALL
    result.stdout.fnmatch_lines(["*3 entries kept because their tests*"])


def test_a_path_subset_keeps_the_other_files(project: pytest.Pytester) -> None:
    _seed(project)
    result = project.runpytest("test_b.py", *UPDATE)
    result.assert_outcomes(passed=1)
    assert _entries(project) == ALL
    result.stdout.fnmatch_lines(["*2 entries kept because their tests*"])


def test_a_node_id_subset_keeps_the_others(project: pytest.Pytester) -> None:
    _seed(project)
    project.runpytest("test_a.py::test_a1", *UPDATE).assert_outcomes(passed=1)
    assert _entries(project) == ALL


def test_testpaths_is_a_full_run(project: pytest.Pytester) -> None:
    project.makeini("[pytest]\nmarkers = slow\ntestpaths = .\n")
    _seed(project)
    project.makepyfile(test_b=HEADER + "def test_b1():\n    pass\n")
    project.runpytest(*UPDATE).assert_outcomes(passed=3)
    assert _entries(project) == {A1, A2}


def test_a_deleted_test_is_removed_on_a_full_run_only(
    project: pytest.Pytester,
) -> None:
    _seed(project)
    (project.path / "test_b.py").unlink()
    project.runpytest("-k", "test_a1", *UPDATE).assert_outcomes(passed=1)
    assert _entries(project) == ALL  # a subset cannot tell deleted from not run
    project.runpytest(*UPDATE).assert_outcomes(passed=2)
    assert _entries(project) == {A1, A2}


def test_a_partial_run_without_an_update_is_unchanged(
    project: pytest.Pytester,
) -> None:
    _seed(project)
    before = (project.path / "qg.json").read_text()
    result = project.runpytest("-k", "test_a1", "--queryguard-baseline=qg.json")
    result.assert_outcomes(passed=1)
    assert (project.path / "qg.json").read_text() == before


@pytest.mark.parametrize("workers", [2])
def test_xdist_subset_keeps_the_others(project: pytest.Pytester, workers: int) -> None:
    pytest.importorskip("xdist")
    run = project.runpytest_subprocess
    run(*UPDATE, "-p", "no:cacheprovider").assert_outcomes(passed=3)
    result = run("-n", str(workers), "-k", "test_a", *UPDATE, "-p", "no:cacheprovider")
    result.assert_outcomes(passed=2)
    assert _entries(project) == ALL
    result.stdout.fnmatch_lines(["*1 entry kept because its test did not run"])
    project.makepyfile(test_a=TEST_A.replace("customer.orders", "customer.id"))
    run("-n", str(workers), "-k", "test_a", *UPDATE, "-p", "no:cacheprovider")
    assert _entries(project) == {A1, B1}
    run("-n", str(workers), *UPDATE, "-p", "no:cacheprovider")
    assert _entries(project) == {A1, B1}
