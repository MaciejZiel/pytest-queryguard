from __future__ import annotations

import importlib.metadata
import os
import warnings as std_warnings
from collections.abc import Generator
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, cast

import pytest

from . import baseline as baselines
from . import catalog, eager, hypopg, indexes, sarif
from .baseline import BaselineError, Identity, sorted_identities
from .collector import QueryCollector, pop, push
from .detectors import DEFAULT_COLUMN_LOAD_THRESHOLD, SCOPE_TEST, SCOPES, detect
from .findings import KIND_MISSING_INDEX, KIND_UNUSED_EAGER_LOAD, Finding
from .ignores import IgnoreRule, apply_rules, check_kind, parse_rules
from .indexes import select_findings
from .listeners import (
    install,
    install_index_check,
    internal_error,
    internal_failure,
    record_error,
    swap_error,
)
from .messages import (
    format_budget,
    format_failure,
    format_findings,
    format_index_findings,
    format_index_notes,
    format_stale_baseline,
    format_stale_ignores,
    format_time_budget,
    format_truncation,
    make_findings,
    make_index_finding,
    swap_root,
)
from .warnings import QueryGuardInternalWarning, QueryGuardWarning, warn_internal

_MODES = ("fail", "warn", "off")
_EAGER_MODES = ("off", "warn", "fail")
_eager_enabled = pytest.StashKey[bool]()
_internal_reported = pytest.StashKey[bool]()
_failure_summarised = pytest.StashKey[bool]()
_outer_error = pytest.StashKey[BaseException | None]()
_outer_stopped = pytest.StashKey[str | None]()
_outer_catalog_error = pytest.StashKey[str | None]()
_outer_no_plpgsql = pytest.StashKey[indexes.EngineSet]()
_outer_root = pytest.StashKey[Path | None]()
_settings = pytest.StashKey["_Settings"]()
_report = pytest.StashKey[list[tuple[str, int]]]()
_index_notes = pytest.StashKey["_IndexNotes"]()
_REPORT_SIZE = 10
# Statements and relationship loads stored per test; generous, a test that
# needs more is far past any N+1 threshold already.
DEFAULT_MAX_EVENTS = 10_000


class _Settings:
    def __init__(
        self,
        mode: str,
        relationship: int,
        repeat: int,
        index_check: bool = False,
        ignore_tables: tuple[str, ...] = (),
        max_events: int | None = DEFAULT_MAX_EVENTS,
        ignore: tuple[IgnoreRule, ...] = (),
        column_load: int = DEFAULT_COLUMN_LOAD_THRESHOLD,
        small_repeat: int | None = None,
        fail_on: tuple[str, ...] = (),
        write_loop: int | None = None,
        unused_eager: str = "off",
        baseline_path: Path | None = None,
        update_baseline: bool = False,
        baseline: frozenset[Identity] | None = None,
        sarif_path: Path | None = None,
        index_advisor: bool = True,
        hypopg: str = "auto",
        repeat_scope: str = SCOPE_TEST,
    ) -> None:
        self.mode = mode
        self.repeat_scope = repeat_scope
        self.relationship = relationship
        self.repeat = repeat
        self.index_check = index_check
        self.ignore_tables = ignore_tables
        self.max_events = max_events
        self.ignore = ignore
        self.column_load = column_load
        self.small_repeat = small_repeat
        self.fail_on = fail_on
        self.write_loop = write_loop
        self.unused_eager = unused_eager
        self.index_advisor = index_advisor
        self.hypopg = hypopg
        self.baseline_path = baseline_path
        self.sarif_path = sarif_path
        self.rootpath: Path | None = None  # for the SARIF location prefix
        # findings that count (not ignored, not baselined), for the SARIF file
        self.reported: sarif.Collected = {}
        self.sarif_result: str | None = None
        self.sarif_failed: str | None = None
        self.update_baseline = update_baseline
        # None: no baseline in use (or one that is about to be rewritten, which
        # then starts empty and is compared with what the run finds)
        self.baseline = baseline
        # identities of the findings the run made (after ignores), baselined or not
        self.seen: set[Identity] = set()
        # the run is incomplete: a worker crashed or stopped measuring
        self.baseline_incomplete = False
        self.baseline_previous_error: str | None = None
        self.baseline_written: str | None = None
        self.baseline_failed: str | None = None
        # what the ignores did, for the stale-ignore warning in the summary
        self.ignore_used: set[IgnoreRule] = set()
        self.stale_markers: list[tuple[str, IgnoreRule]] = []

    @property
    def baselining(self) -> bool:
        return self.baseline_path is not None

    def fails(self, kind: str) -> bool:
        """True when a finding of this kind fails the test; else it only warns."""
        if self.mode != "fail":
            return False
        return not self.fail_on or any(fnmatchcase(kind, p) for p in self.fail_on)


class _IndexNotes:
    """What the index check could not do, summed up over the session."""

    def __init__(self) -> None:
        self.notices: dict[str, None] = {}  # ordered set
        self.errors = 0
        self.partial_skipped: list[tuple[str, ...]] = []
        self.first_error: tuple[str, str] | None = None


# pytest 9 reads native [tool.pytest] TOML, where thresholds are real integers
# and a string-typed option would crash; older versions only know strings.
_NATIVE_INT = pytest.version_tuple >= (9,)
_INT_TYPE: dict[str, Any] = {"type": "int"} if _NATIVE_INT else {}


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("queryguard")
    group.addoption(
        "--queryguard-mode",
        dest="queryguard_mode",
        default=None,
        help="N+1 handling: fail, warn or off (overrides queryguard_mode).",
    )
    group.addoption(
        "--queryguard-report",
        dest="queryguard_report",
        action="store_true",
        default=False,
        help="Print the tests with the most queries at the end of the session.",
    )
    group.addoption(
        "--queryguard-index-check",
        dest="queryguard_index_check",
        action="store_const",
        const=True,
        default=None,
        help="Check statements for a missing index (PostgreSQL 16 or newer, or SQLite); "
        "overrides queryguard_index_check.",
    )
    group.addoption(
        "--no-queryguard-index-check",
        dest="queryguard_index_check",
        action="store_const",
        const=False,
        help="Turn the missing-index check off for this run.",
    )
    group.addoption(
        "--queryguard-baseline",
        dest="queryguard_baseline",
        default=None,
        metavar="PATH",
        help="Baseline file: findings listed in it do not fail or warn "
        "(overrides queryguard_baseline).",
    )
    group.addoption(
        "--queryguard-update-baseline",
        dest="queryguard_update_baseline",
        action="store_true",
        default=False,
        help="Write the findings of this run to the baseline file instead of "
        "reporting them. Run the whole suite to avoid dropping entries.",
    )
    group.addoption(
        "--queryguard-sarif",
        dest="queryguard_sarif",
        default=None,
        metavar="PATH",
        help="Write the findings of this run to PATH as SARIF 2.1.0 (GitHub "
        "code scanning). Findings in the baseline are left out.",
    )
    parser.addini("queryguard_mode", "fail, warn or off", default="fail")
    parser.addini(
        "queryguard_relationship_threshold",
        "Lazy loads of one relationship treated as N+1 (integer >= 2)",
        default=2 if _NATIVE_INT else "2",
        **_INT_TYPE,
    )
    parser.addini(
        "queryguard_repeat_threshold",
        "Executions of one statement treated as N+1 (integer >= 2)",
        default=5 if _NATIVE_INT else "5",
        **_INT_TYPE,
    )
    parser.addini(
        "queryguard_small_repeat_threshold",
        "Executions (below queryguard_repeat_threshold) of one statement from one "
        "source line, each with different parameters, treated as N+1; 0 turns "
        "this off (integer >= 2, or 0)",
        default=0 if _NATIVE_INT else "0",
        **_INT_TYPE,
    )
    parser.addini(
        "queryguard_write_loop_threshold",
        "Row-by-row INSERT, UPDATE or DELETE statements of one shape from one "
        "source line treated as a write loop; 0 turns the check off (integer "
        ">= 2, or 0)",
        default=0 if _NATIVE_INT else "0",
        **_INT_TYPE,
    )
    parser.addini(
        "queryguard_repeat_scope",
        "What the repeat, relationship, column-load and write-loop rules count "
        "together: test (everything one test runs) or session (each SQLAlchemy "
        "Session or connection checkout on its own, for tests that make several "
        "requests)",
        default=SCOPE_TEST,
    )
    parser.addini(
        "queryguard_fail_on",
        "Finding kinds that fail a test in fail mode, one per line (fnmatch "
        "patterns); the other kinds only warn. Empty: all kinds fail",
        type="linelist",
        default=[],
    )
    parser.addini(
        "queryguard_column_load_threshold",
        "Instances of one class that must load deferred or expired columns on "
        "their own to count as N+1 (integer >= 2)",
        default=DEFAULT_COLUMN_LOAD_THRESHOLD
        if _NATIVE_INT
        else str(DEFAULT_COLUMN_LOAD_THRESHOLD),
        **_INT_TYPE,
    )
    parser.addini(
        "queryguard_max_events",
        "Statements stored per test for the report (integer >= 1, or 0 for no limit); "
        "beyond it they are only counted",
        default=DEFAULT_MAX_EVENTS if _NATIVE_INT else str(DEFAULT_MAX_EVENTS),
        **_INT_TYPE,
    )
    parser.addini(
        "queryguard_ignore",
        "Findings to leave out of reports, one per line: "
        "KIND LABEL [file=GLOB] [function=GLOB] (fnmatch patterns)",
        type="linelist",
        default=[],
    )
    parser.addini(
        "queryguard_unused_eager_load",
        "EXPERIMENTAL: report relationships a test loads with selectinload, "
        "joinedload, subqueryload or immediateload and never reads: off "
        "(default), warn or fail",
        default="off",
    )
    parser.addini(
        "queryguard_baseline",
        "Baseline file (relative to the rootdir): findings listed in it do not "
        "fail or warn",
        default="",
    )
    parser.addini(
        "queryguard_index_check",
        "Check statements for a missing index (PostgreSQL 16 or newer, or SQLite)",
        type="bool",
        default=False,
    )
    parser.addini(
        "queryguard_index_advisor",
        "Suggest a CREATE INDEX for each missing-index finding (used with the "
        "index check)",
        type="bool",
        default=True,
    )
    parser.addini(
        "queryguard_hypopg",
        "Verify suggested indexes with the hypopg extension: auto (when "
        "installed), on (also create it inside the check's savepoint) or off",
        default="auto",
    )
    parser.addini(
        "queryguard_index_ignore_tables",
        "Tables (names or fnmatch patterns) the missing-index check ignores",
        type="linelist",
        default=[],
    )


def _threshold(config: pytest.Config, name: str) -> int:
    detail = ""
    try:
        raw: Any = config.getini(name)
    except (TypeError, ValueError) as exc:
        raw, detail = None, f": {exc}"
    try:
        value = 0 if isinstance(raw, bool) else int(raw)
    except (TypeError, ValueError):
        value = 0
    if value < 2:
        shown = detail or f", got {raw!r}"
        raise pytest.UsageError(f"{name} must be an integer >= 2{shown}")
    return value


def _small_repeat(config: pytest.Config, repeat: int) -> int | None:
    name = "queryguard_small_repeat_threshold"
    try:
        raw: Any = config.getini(name)
    except (TypeError, ValueError) as exc:
        raise pytest.UsageError(f"{name} must be 0 or an integer >= 2: {exc}") from None
    try:
        value = -1 if isinstance(raw, bool) else int(raw)
    except (TypeError, ValueError):
        value = -1
    if value == 0:
        return None
    if value < 2:
        raise pytest.UsageError(f"{name} must be 0 or an integer >= 2, got {raw!r}")
    if value >= repeat:
        raise pytest.UsageError(
            f"{name} must be lower than queryguard_repeat_threshold ({repeat}), "
            f"got {value}"
        )
    return value


def _choice(
    config: pytest.Config,
    name: str,
    choices: tuple[str, ...],
    *,
    flag: str | None = None,
) -> str:
    """Read an option with a fixed set of values; case and edge spaces are ignored.

    ``flag`` is the command-line option that overrides the ini option, if any.
    Every enum-valued option goes through here so that they agree on case.
    """
    source = name
    raw: Any = None
    if flag is not None:
        raw = config.getoption(name)
        if raw is not None:
            source = flag
    if raw is None:
        try:
            raw = config.getini(name)
        except (TypeError, ValueError) as exc:
            # pytest 9 rejects a non-string value from native TOML on its own.
            raise pytest.UsageError(
                f"{name} must be one of {', '.join(choices)}: {exc}"
            ) from None
    value = str(raw).strip().lower()
    if value not in choices:
        raise pytest.UsageError(
            f"{source} must be one of {', '.join(choices)}, got {raw!r}"
        )
    return value


def _repeat_scope(config: pytest.Config) -> str:
    return _choice(config, "queryguard_repeat_scope", SCOPES)


def _write_loop(config: pytest.Config) -> int | None:
    name = "queryguard_write_loop_threshold"
    try:
        raw: Any = config.getini(name)
    except (TypeError, ValueError) as exc:
        raise pytest.UsageError(f"{name} must be 0 or an integer >= 2: {exc}") from None
    try:
        value = -1 if isinstance(raw, bool) else int(raw)
    except (TypeError, ValueError):
        value = -1
    if value == 0:
        return None
    if value < 2:
        raise pytest.UsageError(f"{name} must be 0 or an integer >= 2, got {raw!r}")
    return value


def _fail_on(config: pytest.Config) -> tuple[str, ...]:
    name = "queryguard_fail_on"
    try:
        raw: Any = config.getini(name)
    except (TypeError, ValueError) as exc:
        raise pytest.UsageError(f"{name} must be a list of kinds: {exc}") from None
    if not isinstance(raw, list) or not all(isinstance(k, str) for k in raw):
        raise pytest.UsageError(f"{name} must be a list of kinds, got {raw!r}")
    kinds = tuple(k.strip() for k in raw if k.strip() and not k.strip().startswith("#"))
    for kind in kinds:
        try:
            check_kind(kind)
        except ValueError as exc:
            raise pytest.UsageError(f"{name}: {exc}") from None
    return kinds


def _max_events(config: pytest.Config) -> int | None:
    name = "queryguard_max_events"
    try:
        raw: Any = config.getini(name)
    except (TypeError, ValueError) as exc:
        raise pytest.UsageError(f"{name} must be an integer >= 0: {exc}") from None
    try:
        value = -1 if isinstance(raw, bool) else int(raw)
    except (TypeError, ValueError):
        value = -1
    if value < 0:
        raise pytest.UsageError(f"{name} must be an integer >= 0, got {raw!r}")
    return value or None


def _ignore_rules(config: pytest.Config) -> tuple[IgnoreRule, ...]:
    name = "queryguard_ignore"
    try:
        raw: Any = config.getini(name)
    except (TypeError, ValueError) as exc:
        raise pytest.UsageError(f"{name} must be a list of patterns: {exc}") from None
    if not isinstance(raw, list) or not all(isinstance(line, str) for line in raw):
        raise pytest.UsageError(f"{name} must be a list of patterns, got {raw!r}")
    try:
        return parse_rules(raw)
    except ValueError as exc:
        raise pytest.UsageError(f"{name}: {exc}") from None


def _unused_eager(config: pytest.Config) -> str:
    return _choice(config, "queryguard_unused_eager_load", _EAGER_MODES)


def _index_check(config: pytest.Config) -> bool:
    name = "queryguard_index_check"
    try:
        raw: Any = config.getini(name)
    except (TypeError, ValueError) as exc:
        raise pytest.UsageError(f"{name} must be true or false: {exc}") from None
    if not isinstance(raw, bool):
        raise pytest.UsageError(f"{name} must be true or false, got {raw!r}")
    cli: bool | None = config.getoption("queryguard_index_check")
    return raw if cli is None else cli


def _index_advisor(config: pytest.Config) -> bool:
    name = "queryguard_index_advisor"
    try:
        raw: Any = config.getini(name)
    except (TypeError, ValueError) as exc:
        raise pytest.UsageError(f"{name} must be true or false: {exc}") from None
    if not isinstance(raw, bool):
        raise pytest.UsageError(f"{name} must be true or false, got {raw!r}")
    return raw


def _hypopg(config: pytest.Config) -> str:
    return _choice(config, "queryguard_hypopg", tuple(hypopg.MODES))


def _ignore_tables(config: pytest.Config) -> tuple[str, ...]:
    name = "queryguard_index_ignore_tables"
    try:
        raw: Any = config.getini(name)
    except (TypeError, ValueError) as exc:
        raise pytest.UsageError(
            f"{name} must be a list of table names: {exc}"
        ) from None
    if not isinstance(raw, list) or not all(isinstance(t, str) and t for t in raw):
        raise pytest.UsageError(f"{name} must be a list of table names, got {raw!r}")
    return tuple(raw)


def _baseline_path(config: pytest.Config) -> Path | None:
    cli: str | None = config.getoption("queryguard_baseline")
    if cli is not None:
        if not cli.strip():
            raise pytest.UsageError("--queryguard-baseline needs a path")
        return Path(config.invocation_params.dir, cli.strip())
    name = "queryguard_baseline"
    try:
        raw: Any = config.getini(name)
    except (TypeError, ValueError) as exc:
        raise pytest.UsageError(f"{name} must be a path: {exc}") from None
    if not isinstance(raw, str):
        raise pytest.UsageError(f"{name} must be a path, got {raw!r}")
    return Path(config.rootpath, raw.strip()) if raw.strip() else None


def _sarif_path(config: pytest.Config) -> Path | None:
    raw: str | None = config.getoption("queryguard_sarif")
    if raw is None:
        return None
    if not raw.strip():
        raise pytest.UsageError("--queryguard-sarif needs a path")
    path = Path(config.invocation_params.dir, raw.strip())
    config.option.queryguard_sarif = str(path)  # workers get the resolved path
    return path


def _read_settings(config: pytest.Config) -> _Settings:
    mode = _choice(config, "queryguard_mode", _MODES, flag="--queryguard-mode")
    repeat = _threshold(config, "queryguard_repeat_threshold")
    baseline_path = _baseline_path(config)
    update = bool(config.getoption("queryguard_update_baseline"))
    baseline: frozenset[Identity] | None = None
    previous_error: str | None = None
    if update and baseline_path is None:
        raise pytest.UsageError(
            "--queryguard-update-baseline needs a baseline file: pass "
            "--queryguard-baseline=PATH or set queryguard_baseline"
        )
    if update and mode == "off":
        raise pytest.UsageError(
            "--queryguard-update-baseline cannot run with measurement off "
            "(the queryguard mode is off)"
        )
    if baseline_path is not None:
        try:
            baseline = baselines.load(baseline_path)
        except BaselineError as exc:
            if not update:
                raise pytest.UsageError(f"queryguard baseline: {exc}") from None
            # an update replaces a missing or unreadable file
            baseline = frozenset()
            previous_error = str(exc) if baseline_path.exists() else None
        # the CLI path is relative to the invocation directory; workers resolve
        # the same file, whatever their own working directory is
        config.option.queryguard_baseline = str(baseline_path)
    settings = _Settings(
        mode,
        _threshold(config, "queryguard_relationship_threshold"),
        repeat,
        _index_check(config),
        _ignore_tables(config),
        _max_events(config),
        _ignore_rules(config),
        _threshold(config, "queryguard_column_load_threshold"),
        _small_repeat(config, repeat),
        _fail_on(config),
        _write_loop(config),
        _unused_eager(config),
        baseline_path,
        update,
        baseline,
        _sarif_path(config),
        _index_advisor(config),
        _hypopg(config),
        _repeat_scope(config),
    )
    settings.baseline_previous_error = previous_error
    settings.rootpath = config.rootpath
    return settings


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "max_queries(n): fail the test if it runs more than n SQL statements.",
    )
    config.addinivalue_line(
        "markers",
        "max_db_time(seconds): fail the test if its SQL statements take more "
        "than that many seconds in total.",
    )
    config.addinivalue_line(
        "markers",
        "allow_nplusone(*labels, kind=..., file=..., function=...): do not report "
        "N+1 query findings for this test; with arguments, only the findings "
        "they match (fnmatch patterns).",
    )
    config.addinivalue_line(
        "markers",
        "allow_missing_index(*tables): skip the missing-index check for this "
        "test, or only for the given tables.",
    )
    config.stash[_outer_error] = swap_error(None)
    config.stash[_outer_stopped] = indexes.swap_stopped(None)
    config.stash[_outer_catalog_error] = indexes.swap_catalog_error(None)
    config.stash[_outer_no_plpgsql] = indexes.swap_no_plpgsql(indexes.EngineSet())
    config.stash[_outer_root] = swap_root(config.rootpath)
    # A nested session (pytester) may change the database; start afresh.
    catalog.reset()
    settings = _read_settings(config)
    config.stash[_settings] = settings
    if config.getoption("queryguard_report"):
        config.stash[_report] = []
    if settings.mode != "off":
        install()
        if settings.index_check:
            install_index_check()
        if settings.unused_eager != "off":
            eager.enable()
            config.stash[_eager_enabled] = True


def pytest_unconfigure(config: pytest.Config) -> None:
    if config.stash.get(_eager_enabled, False):
        config.stash[_eager_enabled] = False
        eager.disable()
    swap_error(config.stash.get(_outer_error, None))
    indexes.swap_stopped(config.stash.get(_outer_stopped, None))
    indexes.swap_catalog_error(config.stash.get(_outer_catalog_error, None))
    indexes.swap_no_plpgsql(config.stash.get(_outer_no_plpgsql, indexes.EngineSet()))
    swap_root(config.stash.get(_outer_root, None))
    catalog.reset()


def _report_internal_error(config: pytest.Config) -> None:
    error = internal_error()
    if error is None or config.stash.get(_internal_reported, False):
        return
    config.stash[_internal_reported] = True
    failure = internal_failure()
    where = f" ({failure.source})" if failure is not None else ""
    warn_internal(
        QueryGuardInternalWarning(
            f"pytest-queryguard disabled itself after an internal error: {error!r}"
            f"{where}"
        )
    )


def _write_internal_failure(terminalreporter: pytest.TerminalReporter) -> None:
    """Tell the user, once, that measurement stopped, where and why."""
    config = terminalreporter.config
    failure = internal_failure()
    if failure is None or config.stash.get(_failure_summarised, False):
        return
    config.stash[_failure_summarised] = True
    terminalreporter.write_sep("=", "queryguard: measurement stopped", yellow=True)
    for line in format_failure(failure):
        terminalreporter.write_line(line)


def _nplusone_ignores(item: pytest.Item) -> tuple[bool, tuple[IgnoreRule, ...]]:
    """(ignore every N+1 finding, rules) from allow_nplusone markers."""
    ignore_all = False
    rules: list[IgnoreRule] = []
    for marker in item.iter_markers("allow_nplusone"):
        patterns = marker.args
        extra = set(marker.kwargs) - {"kind", "file", "function"}
        values = [*patterns, *marker.kwargs.values()]
        if extra or not all(isinstance(v, str) and v for v in values):
            pytest.fail(
                f"allow_nplusone on {item.nodeid} takes label patterns and the "
                "keywords kind, file and function, all as strings, got "
                f"{marker.args!r} {marker.kwargs!r}",
                pytrace=False,
            )
        if not marker.args and not marker.kwargs:
            ignore_all = True
            continue
        kind = marker.kwargs.get("kind", "*")
        try:
            check_kind(kind)
        except ValueError as exc:
            pytest.fail(f"allow_nplusone on {item.nodeid}: {exc}", pytrace=False)
        for label in patterns or ("*",):
            rules.append(
                IgnoreRule(
                    kind,
                    label,
                    marker.kwargs.get("file"),
                    marker.kwargs.get("function"),
                )
            )
    return ignore_all, tuple(rules)


def _fails(settings: _Settings, finding: Finding) -> bool:
    """True when the finding fails the test; else it is only a warning."""
    if finding.kind == KIND_UNUSED_EAGER_LOAD and settings.unused_eager != "fail":
        return False
    return settings.fails(finding.kind)


def _analyse(
    item: pytest.Item,
    collector: QueryCollector,
    settings: _Settings,
    tracker: eager.Tracker | None = None,
) -> tuple[str | None, str | None]:
    """(report of the failing kinds, report of the others) for the finished test.

    Either is None when empty; never raises.
    """
    if internal_error() is not None:
        return None, None
    ignore_all, marker_rules = _nplusone_ignores(item)
    if ignore_all:
        return None, None
    try:
        root = item.config.rootpath
        findings = make_findings(
            detect(
                collector,
                relationship_threshold=settings.relationship,
                repeat_threshold=settings.repeat,
                column_load_threshold=settings.column_load,
                small_repeat_threshold=settings.small_repeat,
                write_loop_threshold=settings.write_loop,
                scope=settings.repeat_scope,
            ),
            cwd=root,
        )
        if tracker is not None:
            findings.extend(eager.findings(tracker, cwd=root))
        if settings.ignore or marker_rules:
            findings = _drop_ignored(item, settings, findings, marker_rules)
        findings = _apply_baseline(settings, findings)
        failing = [f for f in findings if _fails(settings, f)]
        warning = [f for f in findings if not _fails(settings, f)]
        note = ""
        if collector.truncated and settings.max_events is not None:
            note = "\n\n" + format_truncation(settings.max_events)
        return (
            format_findings(failing, cwd=root) + note if failing else None,
            format_findings(warning, cwd=root) + note if warning else None,
        )
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        record_error(exc, "N+1 analysis")
        return None, None


def _apply_baseline(settings: _Settings, findings: list[Finding]) -> list[Finding]:
    """The findings that still count: not in the baseline, and no update run.

    Records every finding it gets, for the stale-entry report and for an update.
    """
    if settings.baselining:
        settings.seen.update(f.identity for f in findings)
        if settings.update_baseline:
            findings = []
        else:
            findings = baselines.split(findings, settings.baseline or frozenset())[0]
    if settings.sarif_path is not None:
        for finding in findings:
            level = (
                sarif.LEVEL_ERROR if _fails(settings, finding) else sarif.LEVEL_WARNING
            )
            sarif.add(settings.reported, finding, level)
    return findings


def _drop_ignored(
    item: pytest.Item,
    settings: _Settings,
    findings: list[Finding],
    marker_rules: tuple[IgnoreRule, ...],
) -> list[Finding]:
    """The findings the ini patterns and the test's markers do not ignore."""
    kept, used = apply_rules(findings, (*settings.ignore, *marker_rules))
    settings.ignore_used.update(used)
    settings.stale_markers.extend(
        (item.nodeid, rule) for rule in marker_rules if rule not in used
    )
    return kept


def _index_exemption(item: pytest.Item) -> tuple[bool, tuple[str, ...]]:
    """(skip the whole check, tables to ignore) from allow_missing_index markers."""
    exempt_all = False
    tables: list[str] = []
    for marker in item.iter_markers("allow_missing_index"):
        if marker.kwargs or not all(isinstance(t, str) and t for t in marker.args):
            pytest.fail(
                f"allow_missing_index on {item.nodeid} takes table names "
                f"as strings, or nothing, got {marker.args!r} {marker.kwargs!r}",
                pytrace=False,
            )
        if not marker.args:
            exempt_all = True
        tables.extend(marker.args)
    return exempt_all, tuple(tables)


def _analyse_indexes(
    item: pytest.Item,
    collector: QueryCollector,
    settings: _Settings,
    exempt: tuple[str, ...],
) -> str | None:
    """The missing-index report for the finished test, or None; never raises."""
    if internal_error() is not None or not collector.scans:
        return None
    try:
        root = item.config.rootpath
        scans = select_findings(collector.scans, (*settings.ignore_tables, *exempt))
        if (settings.ignore or settings.baselining or settings.sarif_path) and scans:
            findings = [make_index_finding(scan, cwd=root) for scan in scans]
            kept, used = apply_rules(findings, settings.ignore)
            settings.ignore_used.update(used)
            kept = _apply_baseline(settings, kept)
            keep_ids = {id(f) for f in kept}
            scans = [
                scan
                for scan, finding in zip(scans, findings, strict=True)
                if id(finding) in keep_ids
            ]
        if not scans:
            return None
        return format_index_findings(scans, cwd=root)
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        record_error(exc, "missing-index analysis")
        return None


def _note_index_check(config: pytest.Config, collector: QueryCollector) -> None:
    """Add what the index check skipped in this test to the session summary."""
    if not collector.index_check:
        return
    notes = config.stash.setdefault(_index_notes, _IndexNotes())
    notes.notices.update(collector.index_notices)
    notes.partial_skipped.extend(collector.partial_skipped)
    for error in collector.explain_errors:
        notes.errors += 1
        if notes.first_error is None:
            notes.first_error = error


def _budget(item: pytest.Item) -> int | None:
    marker = item.get_closest_marker("max_queries")
    if marker is None:
        return None
    if len(marker.args) > 1 or set(marker.kwargs) - {"n"}:
        pytest.fail(
            f"max_queries on {item.nodeid} takes a single argument, the limit",
            pytrace=False,
        )
    if marker.args and "n" in marker.kwargs:
        pytest.fail(
            f"max_queries on {item.nodeid} got the limit twice, positionally and as n=",
            pytrace=False,
        )
    limit = marker.args[0] if marker.args else marker.kwargs.get("n")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        pytest.fail(
            f"max_queries on {item.nodeid} needs a non-negative integer "
            f"as its limit, got {limit!r}",
            pytrace=False,
        )
    return limit


def _time_budget(item: pytest.Item) -> float | None:
    marker = item.get_closest_marker("max_db_time")
    if marker is None:
        return None
    if len(marker.args) > 1 or set(marker.kwargs) - {"seconds"}:
        pytest.fail(
            f"max_db_time on {item.nodeid} takes a single argument, the limit "
            "in seconds",
            pytrace=False,
        )
    if marker.args and "seconds" in marker.kwargs:
        pytest.fail(
            f"max_db_time on {item.nodeid} got the limit twice, positionally "
            "and as seconds=",
            pytrace=False,
        )
    limit = marker.args[0] if marker.args else marker.kwargs.get("seconds")
    if (
        isinstance(limit, bool)
        or not isinstance(limit, (int, float))
        or not limit >= 0
        or limit == float("inf")
    ):
        pytest.fail(
            f"max_db_time on {item.nodeid} needs a non-negative number of "
            f"seconds as its limit, got {limit!r}",
            pytrace=False,
        )
    return float(limit)


def _time_budget_report(
    item: pytest.Item, collector: QueryCollector, limit: float
) -> str | None:
    """The database-time failure text, or None; a bug here never fails the test."""
    try:
        if collector.db_time <= limit:
            return None
        return format_time_budget(
            limit,
            collector.statements,
            cwd=item.config.rootpath,
            total=collector.statement_count,
            db_time=collector.db_time,
        )
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        record_error(exc, "database-time budget report")
        return None


def _budget_report(
    item: pytest.Item, collector: QueryCollector, limit: int
) -> str | None:
    """The budget failure text, or None; a bug here never fails the test."""
    try:
        if collector.statement_count <= limit:
            return None
        return format_budget(
            limit,
            collector.statements,
            cwd=item.config.rootpath,
            total=collector.statement_count,
            db_time=collector.db_time,
        )
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        record_error(exc, "budget report")
        return None


def _record(item: pytest.Item, collector: QueryCollector, settings: _Settings) -> None:
    """Remember the test's statement count for the session report."""
    counts = item.config.stash.get(_report, None)
    # After an internal error the listeners stop recording, so a count would be wrong.
    if counts is None or settings.mode == "off" or internal_error() is not None:
        return
    counts.append((item.nodeid, collector.statement_count))


def _write_report(
    terminalreporter: pytest.TerminalReporter, settings: _Settings
) -> None:
    terminalreporter.write_sep("=", "queryguard report")
    # dsession is registered only on the xdist controller when it distributes
    # tests; the counts live in the workers, so the report would be empty.
    if terminalreporter.config.pluginmanager.has_plugin("dsession"):
        terminalreporter.write_line(
            "queryguard report: not available with pytest-xdist"
        )
        return
    if settings.mode == "off":
        terminalreporter.write_line(
            "queryguard report: measurement is off (queryguard_mode = off)"
        )
        return
    if internal_error() is not None:
        terminalreporter.write_line(
            "queryguard report: incomplete, measurement stopped after an internal error"
        )
    counts = terminalreporter.config.stash.get(_report, [])
    if not counts:
        terminalreporter.write_line("no tests were measured")
        return
    # sorted() is stable, so tests with equal counts keep the order they ran in.
    heaviest = sorted(counts, key=lambda entry: entry[1], reverse=True)
    for nodeid, count in heaviest[:_REPORT_SIZE]:
        terminalreporter.write_line(f"{count:>5}  {nodeid}")


def _write_index_notes(terminalreporter: pytest.TerminalReporter) -> None:
    notes = terminalreporter.config.stash.get(_index_notes, None)
    if notes is None or not (notes.notices or notes.errors or notes.partial_skipped):
        return
    terminalreporter.write_sep("=", "queryguard index check")
    for line in format_index_notes(
        notes.notices, notes.errors, notes.first_error, notes.partial_skipped
    ):
        terminalreporter.write_line(line)


def _write_stale_ignores(
    terminalreporter: pytest.TerminalReporter, settings: _Settings
) -> None:
    """Warn about ignores that matched no finding in this run."""
    config = terminalreporter.config
    # The ini patterns match in the workers, so the controller cannot tell.
    # After an internal error or with measurement off, nothing was matched.
    if (
        config.pluginmanager.has_plugin("dsession")
        or settings.mode == "off"
        or internal_error() is not None
    ):
        return
    stale = [rule for rule in settings.ignore if rule not in settings.ignore_used]
    lines = format_stale_ignores(stale, settings.stale_markers)
    if lines:
        terminalreporter.write_sep("=", "queryguard: stale ignores", yellow=True)
        for line in lines:
            terminalreporter.write_line(line)


def _is_worker(config: pytest.Config) -> bool:
    return hasattr(config, "workerinput")


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node: Any, error: object) -> None:
    """pytest-xdist, on the controller: collect what a worker found."""
    settings = node.config.stash.get(_settings, None)
    if settings is None or not (settings.baselining or settings.sarif_path):
        return
    output = getattr(node, "workeroutput", None) or {}
    if error is not None or output.get("queryguard_incomplete", True):
        settings.baseline_incomplete = True
    for identity in output.get("queryguard_seen", []):
        settings.seen.add(_identity(identity))
    sarif.merge_wire(settings.reported, output.get("queryguard_reported", []))


def _identity(raw: Any) -> Identity:
    kind, label, file, function = raw
    return (kind, label, file, function)


_ABNORMAL_EXIT = frozenset(
    (
        pytest.ExitCode.INTERRUPTED,
        pytest.ExitCode.USAGE_ERROR,
        pytest.ExitCode.INTERNAL_ERROR,
    )
)


def pytest_sessionfinish(
    session: pytest.Session, exitstatus: int | pytest.ExitCode
) -> None:
    config = session.config
    settings = config.stash.get(_settings, None)
    if settings is None or not (settings.baselining or settings.sarif_path):
        return
    if _is_worker(config):
        # sent to the controller when the worker finishes
        output = cast(Any, config).workeroutput
        output["queryguard_seen"] = [
            list(identity) for identity in sorted_identities(settings.seen)
        ]
        output["queryguard_reported"] = sarif.to_wire(settings.reported)
        output["queryguard_incomplete"] = internal_error() is not None
        return
    if internal_error() is not None:
        settings.baseline_incomplete = True
    interrupted = exitstatus in _ABNORMAL_EXIT
    if settings.update_baseline:
        _update_baseline(session, settings, interrupted)
    if settings.sarif_path is not None:
        if interrupted:
            # a partial report would close the alerts of the tests that never ran
            settings.sarif_failed = (
                "not written: the run was interrupted or did not finish "
                "(collection error, Ctrl-C or internal error); "
                "an existing file is left as it was"
            )
        else:
            _write_sarif(settings)


def _write_sarif(settings: _Settings) -> None:
    path = settings.sarif_path
    if path is None:
        return
    try:
        version = importlib.metadata.version("pytest-queryguard")
    except importlib.metadata.PackageNotFoundError:
        version = "unknown"
    try:
        prefix = sarif.workspace_prefix(
            settings.rootpath, os.environ.get("GITHUB_WORKSPACE")
        )
        document, omitted = sarif.build(settings.reported, version, prefix)
        sarif.write(path, document)
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        settings.sarif_failed = f"could not write {path}: {exc}"
        return
    count = len(document["runs"][0]["results"])
    text = f"{path}: {count} results"
    if omitted:
        text += f" ({omitted} left out: no location inside the rootdir)"
    settings.sarif_result = text


def _update_baseline(
    session: pytest.Session, settings: _Settings, interrupted: bool = False
) -> None:
    path = settings.baseline_path
    if path is None:
        return
    if interrupted:
        settings.baseline_failed = (
            "not updated: the run was interrupted or did not finish "
            "(collection error, Ctrl-C or internal error)"
        )
        return
    if session.shouldstop or session.shouldfail or settings.baseline_incomplete:
        settings.baseline_failed = (
            "not updated: the run stopped early or measurement failed"
        )
        return
    before = settings.baseline or frozenset()
    after = frozenset(settings.seen)
    try:
        baselines.write(path, after)
    except OSError as exc:
        settings.baseline_failed = f"could not write {path}: {exc}"
        return
    settings.baseline_written = (
        f"{path}: {len(after)} entries ({len(after - before)} added, "
        f"{len(before - after)} removed)"
    )


def _write_baseline_summary(
    terminalreporter: pytest.TerminalReporter, settings: _Settings
) -> None:
    if not settings.baselining:
        return
    if settings.update_baseline:
        if settings.baseline_failed is not None:
            terminalreporter.write_sep("=", "queryguard: baseline", red=True)
            terminalreporter.write_line(
                f"queryguard baseline {settings.baseline_failed}"
            )
        elif settings.baseline_written is not None:
            terminalreporter.write_sep("=", "queryguard: baseline updated")
            terminalreporter.write_line(settings.baseline_written)
            if settings.baseline_previous_error is not None:
                terminalreporter.write_line(
                    f"replaced an unusable file ({settings.baseline_previous_error})"
                )
        return
    if (
        settings.mode == "off"
        or settings.baseline_incomplete
        or internal_error() is not None
        or settings.baseline_path is None
    ):
        return
    stale = baselines.sorted_identities(
        (settings.baseline or frozenset()) - settings.seen
    )
    lines = format_stale_baseline(stale, settings.baseline_path)
    if lines:
        terminalreporter.write_sep(
            "=", "queryguard: stale baseline entries", yellow=True
        )
        for line in lines:
            terminalreporter.write_line(line)


def _write_sarif_summary(
    terminalreporter: pytest.TerminalReporter, settings: _Settings
) -> None:
    if settings.sarif_failed is not None:
        terminalreporter.write_sep("=", "queryguard: SARIF", red=True)
        terminalreporter.write_line(f"queryguard SARIF {settings.sarif_failed}")
    elif settings.sarif_result is not None:
        terminalreporter.write_sep("=", "queryguard: SARIF written")
        terminalreporter.write_line(settings.sarif_result)


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    config = terminalreporter.config
    settings = config.stash.get(_settings, None)
    if settings is None:
        return
    try:
        _write_index_notes(terminalreporter)
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        record_error(exc, "summary of the index check")
    if config.getoption("queryguard_report"):
        try:
            _write_report(terminalreporter, settings)
        except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
            record_error(exc, "queryguard report")
    try:
        _write_stale_ignores(terminalreporter, settings)
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        record_error(exc, "stale-ignore summary")
    try:
        _write_baseline_summary(terminalreporter, settings)
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        record_error(exc, "baseline summary")
    try:
        _write_sarif_summary(terminalreporter, settings)
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        record_error(exc, "SARIF summary")
    _report_internal_error(config)
    try:
        _write_internal_failure(terminalreporter)
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        record_error(exc, "failure summary")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, Any, Any]:
    settings = item.config.stash.get(_settings, None)
    if settings is None:
        # pytest_configure did not complete (for example it raised a UsageError
        # for a bad option), so there is nothing to measure with.
        return (yield)
    limit = _budget(item)
    time_limit = _time_budget(item)
    if settings.mode == "off" and limit is None and time_limit is None:
        return (yield)
    index_check = settings.mode != "off" and settings.index_check
    exempt_all, exempt = _index_exemption(item) if index_check else (False, ())
    if limit is not None or time_limit is not None:
        install()  # idempotent; budgets also work when the mode is off
    collector = QueryCollector()
    collector.max_events = settings.max_events
    collector.index_check = index_check and not exempt_all
    collector.index_advisor = settings.index_advisor
    collector.hypopg = settings.hypopg
    push(collector)
    tracker = eager.start() if settings.mode != "off" and eager.is_enabled() else None
    try:
        result = yield
    finally:
        if tracker is not None:
            eager.stop(tracker)
        pop(collector)
        _report_internal_error(item.config)
        _record(item, collector, settings)  # failing tests are reported too
        _note_index_check(item.config, collector)
    parts: list[str] = []
    if limit is not None:
        budget = _budget_report(item, collector, limit)
        if budget is not None:
            parts.append(budget)
    if time_limit is not None:
        time_budget = _time_budget_report(item, collector, time_limit)
        if time_budget is not None:
            parts.append(time_budget)
    fail_texts: list[str] = []
    warn_texts: list[str] = []
    if settings.mode != "off":
        failing, warning = _analyse(item, collector, settings, tracker)
        index = _analyse_indexes(item, collector, settings, exempt)
        if index is not None:
            (fail_texts if settings.fails(KIND_MISSING_INDEX) else warn_texts).append(
                index
            )
        if failing is not None:
            fail_texts.insert(0, failing)
        if warning is not None:
            warn_texts.insert(0, warning)
    _report_internal_error(item.config)
    if parts or fail_texts:
        # one failure: budgets first, then N+1 and index findings, then the
        # findings of kinds that would only have warned
        parts.extend(fail_texts)
        parts.extend(warn_texts)
    else:
        path, line, _ = item.reportinfo()
        for text in warn_texts:
            std_warnings.warn_explicit(
                QueryGuardWarning(text),
                QueryGuardWarning,
                str(path),
                (line or 0) + 1,
            )
    if parts:
        pytest.fail("\n\n".join(parts), pytrace=False)
    return result
