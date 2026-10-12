from __future__ import annotations

import json
from pathlib import Path

import pytest

from pytest_queryguard import baseline, fileio
from pytest_queryguard.baseline import BaselineError

# pytester runs sessions in-process, so the outer plugin sees their queries too.
pytestmark = pytest.mark.allow_nplusone

MODELS = (Path(__file__).parent / "models.py").read_text()

LAZY_ID = ("relationship-load", "Order.customer", "test_a.py", "test_loop")

TWO_LOOPS = """
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
ONE_LOOP = TWO_LOOPS.split("def test_other_loop")[0]
CLEAN = "def test_clean():\n    pass\n"


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makepyfile(qgmodels=MODELS)
    return pytester


def _read(path: Path) -> dict[str, object]:
    return json.loads(path.read_text())  # type: ignore[no-any-return]


# --- the file format ---


def test_dumps_is_sorted_and_stable() -> None:
    a = ("write-loop", "INSERT", "b.py", "f")
    b = ("relationship-load", "X.y", "a.py", None)
    c = ("relationship-load", "X.y", None, None)
    first = baseline.dumps([a, b, c])
    assert first == baseline.dumps([c, a, b, a])
    assert first.endswith("}\n")
    document = json.loads(first)
    assert document["version"] == 1
    assert [e["file"] for e in document["findings"]] == [None, "a.py", "b.py"]


def test_round_trip_keeps_missing_file_and_function() -> None:
    identities = {("missing-index", "public.orders", None, None), LAZY_ID}
    text = baseline.dumps(identities)
    assert baseline.parse(text, Path("b.json")) == identities


def test_write_uses_unix_newlines_and_leaves_no_temp_file(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "baseline.json"
    baseline.write(path, [LAZY_ID])
    assert b"\r" not in path.read_bytes()
    assert baseline.load(path) == {LAZY_ID}
    assert [p.name for p in path.parent.iterdir()] == ["baseline.json"]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("{not json", "not valid JSON"),
        ("[]", "not a queryguard baseline"),
        ('{"findings": []}', "not a queryguard baseline"),
        ('{"version": 2, "findings": []}', "version 2, but this pytest-queryguard"),
        ('{"version": true, "findings": []}', "version True"),
        ('{"version": 1}', "'findings' must be a list"),
        ('{"version": 1, "findings": [1]}', r"findings\[0\] must be an object"),
        (
            '{"version": 1, "findings": [{"kind": "k", "label": "l", "line": 3}]}',
            "unknown keys: line",
        ),
        ('{"version": 1, "findings": [{"label": "l"}]}', "'kind' must be a non-empty"),
        (
            '{"version": 1, "findings": [{"kind": "k", "label": "l", "file": 1}]}',
            "'file' must be a string or null",
        ),
    ],
)
def test_invalid_files_are_explained(text: str, message: str) -> None:
    with pytest.raises(BaselineError, match=message):
        baseline.parse(text, Path("b.json"))


def test_missing_file_says_how_to_create_it(tmp_path: Path) -> None:
    with pytest.raises(BaselineError, match="--queryguard-update-baseline"):
        baseline.load(tmp_path / "nope.json")


def test_unreadable_file_is_an_error_not_a_crash(tmp_path: Path) -> None:
    with pytest.raises(BaselineError, match="cannot read"):
        baseline.load(tmp_path)  # a directory


def test_deeply_nested_json_is_an_error_not_a_crash(tmp_path: Path) -> None:
    # json raises RecursionError, not ValueError, for this
    path = tmp_path / "qg.json"
    path.write_text("[" * 200_000)
    with pytest.raises(BaselineError, match="nested too deep"):
        baseline.load(path)


# --- the plugin ---


def test_update_writes_the_findings_and_the_run_passes(
    project: pytest.Pytester,
) -> None:
    project.makepyfile(test_a=TWO_LOOPS)
    result = project.runpytest(
        "--queryguard-baseline=qg.json", "--queryguard-update-baseline"
    )
    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(
        ["*queryguard: baseline updated*", "*2 entries (2 added, 0 removed)"]
    )
    entries = _read(project.path / "qg.json")["findings"]
    assert {(e["kind"], e["label"], e["file"], e["function"]) for e in entries} == {  # type: ignore[attr-defined,union-attr]
        LAZY_ID,
        ("relationship-load", "Customer.orders", "test_a.py", "test_other_loop"),
    }


def test_a_baselined_finding_does_not_fail(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=ONE_LOOP)
    project.runpytest("--queryguard-baseline=qg.json", "--queryguard-update-baseline")
    result = project.runpytest("--queryguard-baseline=qg.json")
    result.assert_outcomes(passed=1)
    result.stdout.no_fnmatch_line("*stale baseline*")
    result.stdout.no_fnmatch_line("*N+1 detected*")


def test_a_baselined_finding_does_not_warn_in_warn_mode(
    project: pytest.Pytester,
) -> None:
    project.makepyfile(test_a=ONE_LOOP)
    project.runpytest("--queryguard-baseline=qg.json", "--queryguard-update-baseline")
    result = project.runpytest(
        "--queryguard-baseline=qg.json", "--queryguard-mode=warn"
    )
    result.assert_outcomes(passed=1)
    assert "warnings" not in result.stdout.str()


def test_a_new_finding_still_fails(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=ONE_LOOP)
    project.runpytest("--queryguard-baseline=qg.json", "--queryguard-update-baseline")
    project.makepyfile(test_a=TWO_LOOPS)
    result = project.runpytest("--queryguard-baseline=qg.json")
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(["*Customer.orders was lazy-loaded*"])
    result.stdout.no_fnmatch_line("*Order.customer was lazy-loaded*")


def test_line_numbers_and_counts_do_not_matter(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=ONE_LOOP)
    project.runpytest("--queryguard-baseline=qg.json", "--queryguard-update-baseline")
    # same finding, moved down and with more rows
    project.makepyfile(test_a="# padding\n" * 20 + ONE_LOOP)
    result = project.runpytest("--queryguard-baseline=qg.json")
    result.assert_outcomes(passed=1)


def test_the_same_label_in_another_function_is_new(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=ONE_LOOP)
    project.runpytest("--queryguard-baseline=qg.json", "--queryguard-update-baseline")
    project.makepyfile(test_a=ONE_LOOP.replace("def test_loop", "def test_renamed"))
    result = project.runpytest("--queryguard-baseline=qg.json")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*stale baseline entries*"])


def test_entries_that_no_longer_occur_are_reported(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=TWO_LOOPS)
    project.runpytest("--queryguard-baseline=qg.json", "--queryguard-update-baseline")
    project.makepyfile(test_a=ONE_LOOP)
    result = project.runpytest("--queryguard-baseline=qg.json")
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(
        [
            "*queryguard: stale baseline entries*",
            "1 baseline entry in *qg.json matched no finding in this run:",
            "  relationship-load Customer.orders (test_a.py, test_other_loop)",
            "*--queryguard-update-baseline.",
        ]
    )
    assert result.ret == 0


def test_update_prunes_stale_entries_and_keeps_the_file_stable(
    project: pytest.Pytester,
) -> None:
    project.makepyfile(test_a=TWO_LOOPS)
    project.runpytest("--queryguard-baseline=qg.json", "--queryguard-update-baseline")
    project.makepyfile(test_a=ONE_LOOP)
    result = project.runpytest(
        "--queryguard-baseline=qg.json", "--queryguard-update-baseline"
    )
    result.stdout.fnmatch_lines(["*1 entries (0 added, 1 removed)"])
    first = (project.path / "qg.json").read_text()
    project.runpytest("--queryguard-baseline=qg.json", "--queryguard-update-baseline")
    assert (project.path / "qg.json").read_text() == first


def test_ignored_findings_stay_out_of_the_baseline(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=ONE_LOOP)
    project.makeini(
        "[pytest]\nqueryguard_ignore =\n    relationship-load Order.customer\n"
    )
    project.runpytest("--queryguard-baseline=qg.json", "--queryguard-update-baseline")
    assert _read(project.path / "qg.json")["findings"] == []


def test_ini_option_is_relative_to_the_rootdir(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=ONE_LOOP)
    project.makeini("[pytest]\nqueryguard_baseline = config/qg.json\n")
    project.runpytest("--queryguard-update-baseline")
    assert (project.path / "config" / "qg.json").exists()
    assert project.runpytest().ret == 0


def test_the_option_beats_the_ini_file_and_is_relative_to_the_invocation_dir(
    project: pytest.Pytester,
) -> None:
    project.makepyfile(test_a=ONE_LOOP)
    project.makeini("[pytest]\nqueryguard_baseline = ini.json\n")
    project.runpytest("--queryguard-baseline=cli.json", "--queryguard-update-baseline")
    assert (project.path / "cli.json").exists()
    assert not (project.path / "ini.json").exists()


def test_no_baseline_means_no_changes(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=ONE_LOOP)
    result = project.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.no_fnmatch_line("*queryguard*baseline*")


def test_a_missing_file_is_a_usage_error(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=CLEAN)
    result = project.runpytest("--queryguard-baseline=nope.json")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(
        ["*queryguard baseline: baseline file *nope.json does not exist*"]
    )


def test_a_corrupt_file_is_a_usage_error(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=CLEAN)
    (project.path / "qg.json").write_text("{oops")
    result = project.runpytest("--queryguard-baseline=qg.json")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*qg.json is not valid JSON*"])


def test_an_unknown_version_is_a_usage_error(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=CLEAN)
    (project.path / "qg.json").write_text('{"version": 9, "findings": []}')
    result = project.runpytest("--queryguard-baseline=qg.json")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*has version 9*"])


def test_update_replaces_a_corrupt_file(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=ONE_LOOP)
    (project.path / "qg.json").write_text("{oops")
    result = project.runpytest(
        "--queryguard-baseline=qg.json", "--queryguard-update-baseline"
    )
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(["*replaced an unusable file*"])
    assert len(_read(project.path / "qg.json")["findings"]) == 1  # type: ignore[arg-type]


def test_update_needs_a_path(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=CLEAN)
    result = project.runpytest("--queryguard-update-baseline")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*needs a baseline file*"])


def test_update_cannot_run_with_measurement_off(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=CLEAN)
    result = project.runpytest(
        "--queryguard-baseline=qg.json",
        "--queryguard-update-baseline",
        "--queryguard-mode=off",
    )
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*cannot run with measurement off*"])


def test_an_empty_path_is_a_usage_error(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=CLEAN)
    result = project.runpytest("--queryguard-baseline=")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*--queryguard-baseline needs a path*"])


def test_update_after_an_early_stop_leaves_the_file_alone(
    project: pytest.Pytester,
) -> None:
    project.makepyfile(test_a="def test_boom():\n    assert False\n" + ONE_LOOP)
    path = project.path / "qg.json"
    path.write_text(baseline.dumps([LAZY_ID, ("write-loop", "x", None, None)]))
    before = path.read_text()
    result = project.runpytest(
        "--queryguard-baseline=qg.json", "--queryguard-update-baseline", "-x"
    )
    result.assert_outcomes(failed=1)
    assert path.read_text() == before
    result.stdout.fnmatch_lines(["*queryguard baseline not updated: the run stopped*"])


def test_a_write_error_is_reported_not_raised(
    project: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(path: Path, identities: object) -> None:
        raise PermissionError("read-only")

    monkeypatch.setattr(baseline, "write", fail)
    project.makepyfile(test_a=ONE_LOOP)
    result = project.runpytest(
        "--queryguard-baseline=qg.json", "--queryguard-update-baseline"
    )
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(["*could not write *qg.json: read-only*"])


def test_a_deeply_nested_file_is_a_usage_error(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=CLEAN)
    (project.path / "qg.json").write_text("[" * 200_000)
    result = project.runpytest("--queryguard-baseline=qg.json")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*qg.json is not a queryguard baseline*"])


def test_an_update_replaces_a_deeply_nested_file(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=ONE_LOOP)
    (project.path / "qg.json").write_text("[" * 200_000)
    result = project.runpytest(
        "--queryguard-baseline=qg.json", "--queryguard-update-baseline"
    )
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(["*replaced an unusable file*nested too deep*"])
    assert baseline.load(project.path / "qg.json")


@pytest.mark.parametrize("error", [RuntimeError, MemoryError, UnicodeEncodeError])
def test_any_write_error_is_reported_not_raised(
    project: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, error: type
) -> None:
    def fail(path: Path, identities: object) -> None:
        if error is UnicodeEncodeError:
            raise UnicodeEncodeError("utf-8", "\udc80", 0, 1, "surrogates not allowed")
        raise error("broken")

    monkeypatch.setattr(baseline, "write", fail)
    project.makepyfile(test_a=ONE_LOOP)
    result = project.runpytest(
        "--queryguard-baseline=qg.json", "--queryguard-update-baseline"
    )
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(["*queryguard baseline could not write *qg.json*"])
    assert "Traceback" not in result.stdout.str() + result.stderr.str()


def test_stale_entries_are_not_reported_with_measurement_off(
    project: pytest.Pytester,
) -> None:
    project.makepyfile(test_a=ONE_LOOP)
    project.runpytest("--queryguard-baseline=qg.json", "--queryguard-update-baseline")
    result = project.runpytest("--queryguard-baseline=qg.json", "--queryguard-mode=off")
    result.stdout.no_fnmatch_line("*stale baseline*")


def test_stale_list_is_capped(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=CLEAN)
    many = [("write-loop", f"INSERT INTO t{i}", "a.py", "f") for i in range(25)]
    (project.path / "qg.json").write_text(baseline.dumps(many))
    result = project.runpytest("--queryguard-baseline=qg.json")
    result.stdout.fnmatch_lines(["25 baseline entries in *", "*... and 5 more"])


def _with_scan(monkeypatch: pytest.MonkeyPatch, table: str) -> None:
    from pytest_queryguard import plugin
    from pytest_queryguard.collector import QueryCollector
    from pytest_queryguard.events import ScanEvent, ScanIssue

    issue = ScanIssue("public", table, "Seq Scan", "filter", "(a = $1)", "a")
    real = QueryCollector

    def make() -> QueryCollector:
        collector = real()
        collector.scans.append(ScanEvent(issue, "SELECT 1", None))
        return collector

    monkeypatch.setattr(plugin, "QueryCollector", make)


def test_missing_index_findings_can_be_baselined(
    project: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_scan(monkeypatch, "orders")
    project.makepyfile(test_a=CLEAN)
    args = ("--queryguard-index-check", "--queryguard-baseline=qg.json")
    project.runpytest(*args, "--queryguard-update-baseline")
    entries = _read(project.path / "qg.json")["findings"]
    assert entries == [  # type: ignore[comparison-overlap]
        {
            "kind": "missing-index",
            "label": "public.orders",
            "file": None,
            "function": None,
        }
    ]
    project.runpytest(*args).assert_outcomes(passed=1)
    _with_scan(monkeypatch, "invoices")
    result = project.runpytest(*args)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        ["*public.invoices is read in full*", "*stale baseline*"]
    )


def test_a_failed_write_removes_its_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(fileio.os, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        baseline.write(tmp_path / "qg.json", [LAZY_ID])
    assert list(tmp_path.iterdir()) == []
