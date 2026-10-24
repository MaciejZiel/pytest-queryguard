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


def _nested(project: pytest.Pytester, ini: str) -> None:
    """test_a.py in tests/, test_b.py in other/, qgmodels importable from the root."""
    for name, source in (("tests/test_a.py", TEST_A), ("other/test_b.py", TEST_B)):
        target = project.path / name
        target.parent.mkdir(exist_ok=True)
        target.write_text(source)
    (project.path / "test_a.py").unlink()
    (project.path / "test_b.py").unlink()
    project.makeini("[pytest]\nmarkers = slow\npythonpath = .\n" + ini)


NESTED_A = {
    (kind, label, "tests/" + file, func) for kind, label, file, func in (A1, A2)
}
NESTED_B = ("relationship-load", "Order.customer", "other/test_b.py", "test_b1")


@pytest.mark.parametrize("args", [(), ("tests",), ("tests/",), ("./tests",)])
def test_arguments_that_cover_testpaths_are_a_full_run(
    project: pytest.Pytester, args: tuple[str, ...]
) -> None:
    _nested(project, "testpaths = tests\n")
    project.runpytest(*UPDATE).assert_outcomes(passed=2)
    assert _entries(project) == NESTED_A
    (project.path / "tests" / "test_a.py").write_text(
        TEST_A.replace("customer.orders", "customer.id").replace(
            "def test_a1", "def test_renamed"
        )
    )
    result = project.runpytest(*args, *UPDATE)
    result.assert_outcomes(passed=2)
    # test_a1 was renamed: only a full run can drop its entry
    assert _entries(project) == {
        ("relationship-load", "Order.customer", "tests/test_a.py", "test_renamed")
    }
    result.stdout.no_fnmatch_line("*kept because*")


@pytest.mark.parametrize("absolute", [False, True])
def test_the_rootdir_as_argument_is_a_full_run(
    project: pytest.Pytester, absolute: bool
) -> None:
    _nested(project, "testpaths = tests\n")
    project.runpytest(*UPDATE).assert_outcomes(passed=2)
    (project.path / "tests" / "test_a.py").write_text(
        HEADER + "def test_a1():\n    pass\n"
    )
    (project.path / "tests" / "test_a.py").rename(project.path / "tests" / "test_c.py")
    root = str(project.path) if absolute else "."
    # the rootdir is wider than testpaths, so other/ runs too
    project.runpytest(root, *UPDATE).assert_outcomes(passed=2)
    assert _entries(project) == {NESTED_B}


def test_one_of_several_testpaths_keeps_the_others(project: pytest.Pytester) -> None:
    _nested(project, "testpaths =\n    tests\n    other\n")
    project.runpytest(*UPDATE).assert_outcomes(passed=3)
    assert _entries(project) == NESTED_A | {NESTED_B}
    result = project.runpytest("tests", *UPDATE)
    result.assert_outcomes(passed=2)
    assert _entries(project) == NESTED_A | {NESTED_B}
    result.stdout.fnmatch_lines(["*1 entry kept because its test did not run"])


def test_collect_only_does_not_touch_the_file(project: pytest.Pytester) -> None:
    _seed(project)
    before = (project.path / "qg.json").read_text()
    result = project.runpytest("--collect-only", *UPDATE)
    assert (project.path / "qg.json").read_text() == before
    result.stdout.fnmatch_lines(["*not updated: --collect-only runs no tests"])


def test_a_hook_that_drops_tests_silently_keeps_their_entries(
    project: pytest.Pytester,
) -> None:
    _seed(project)
    project.makeconftest(
        "def pytest_collection_modifyitems(items):\n"
        "    items[:] = [i for i in items if 'test_b' not in i.nodeid]\n"
    )
    result = project.runpytest(*UPDATE)
    result.assert_outcomes(passed=2)
    assert _entries(project) == ALL
    result.stdout.fnmatch_lines(["*1 entry kept because its test did not run"])


def test_ignore_on_the_command_line_keeps_the_ignored_file(
    project: pytest.Pytester,
) -> None:
    _seed(project)
    project.runpytest("--ignore=test_b.py", *UPDATE).assert_outcomes(passed=2)
    assert _entries(project) == ALL


def test_ignore_in_ini_addopts_is_part_of_the_suite(project: pytest.Pytester) -> None:
    _seed(project)
    project.makeini("[pytest]\nmarkers = slow\naddopts = --ignore=test_b.py\n")
    result = project.runpytest(*UPDATE)
    result.assert_outcomes(passed=2)
    assert _entries(project) == {A1, A2}
    result.stdout.no_fnmatch_line("*kept because*")


def test_ignore_in_pytest_addopts_keeps_the_ignored_file(
    project: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(project)
    monkeypatch.setenv("PYTEST_ADDOPTS", "--ignore test_b.py")
    project.runpytest(*UPDATE).assert_outcomes(passed=2)
    assert _entries(project) == ALL


@pytest.mark.parametrize("dist", ["load", "loadfile", "loadgroup", "loadscope"])
def test_xdist_four_workers_subset_keeps_the_others(
    project: pytest.Pytester, dist: str
) -> None:
    pytest.importorskip("xdist")
    run = project.runpytest_subprocess
    extra = ("-n", "4", "--dist", dist, "-p", "no:cacheprovider")
    run(*UPDATE, *extra).assert_outcomes(passed=3)
    assert _entries(project) == ALL
    run("-k", "test_a1 or test_b1", *UPDATE, *extra).assert_outcomes(passed=2)
    assert _entries(project) == ALL
    run("test_b.py", *UPDATE, *extra).assert_outcomes(passed=1)
    assert _entries(project) == ALL
    (project.path / "test_b.py").unlink()
    run(*UPDATE, *extra).assert_outcomes(passed=2)
    assert _entries(project) == {A1, A2}


def test_a_marker_filter_in_addopts_keeps_the_deselected_entries(
    project: pytest.Pytester,
) -> None:
    _seed(project)
    project.makeini("[pytest]\nmarkers = slow\naddopts = -m 'not slow'\n")
    result = project.runpytest(*UPDATE)
    result.assert_outcomes(passed=2)
    assert _entries(project) == ALL
    result.stdout.fnmatch_lines(["*1 entry kept because its test did not run"])


def test_running_from_a_testpaths_directory_is_a_full_run(
    project: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _nested(project, "testpaths = tests\n")
    project.runpytest(*UPDATE).assert_outcomes(passed=2)
    (project.path / "tests" / "test_a.py").write_text(
        HEADER + "def test_a1():\n    pass\n"
    )
    monkeypatch.chdir(project.path / "tests")
    project.runpytest(
        "--queryguard-baseline=../qg.json", "--queryguard-update-baseline"
    ).assert_outcomes(passed=1)
    assert _entries(project) == set()
