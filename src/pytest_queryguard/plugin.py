from __future__ import annotations

import warnings as std_warnings
from collections.abc import Generator
from typing import Any

import pytest

from . import indexes
from .collector import QueryCollector, pop, push
from .detectors import detect
from .indexes import select_findings
from .listeners import (
    install,
    install_index_check,
    internal_error,
    record_error,
    swap_error,
)
from .messages import (
    format_budget,
    format_findings,
    format_index_findings,
    format_index_notes,
)
from .warnings import QueryGuardInternalWarning, QueryGuardWarning

_MODES = ("fail", "warn", "off")
_internal_reported = pytest.StashKey[bool]()
_outer_error = pytest.StashKey[BaseException | None]()
_outer_stopped = pytest.StashKey[str | None]()
_settings = pytest.StashKey["_Settings"]()
_report = pytest.StashKey[list[tuple[str, int]]]()
_index_notes = pytest.StashKey["_IndexNotes"]()
_REPORT_SIZE = 10


class _Settings:
    def __init__(
        self,
        mode: str,
        relationship: int,
        repeat: int,
        index_check: bool = False,
        ignore_tables: tuple[str, ...] = (),
    ) -> None:
        self.mode = mode
        self.relationship = relationship
        self.repeat = repeat
        self.index_check = index_check
        self.ignore_tables = ignore_tables


class _IndexNotes:
    """What the index check could not do, summed up over the session."""

    def __init__(self) -> None:
        self.notices: dict[str, None] = {}  # ordered set
        self.errors = 0
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
        help="Check each SELECT for a missing index (PostgreSQL 16 or newer); "
        "overrides queryguard_index_check.",
    )
    group.addoption(
        "--no-queryguard-index-check",
        dest="queryguard_index_check",
        action="store_const",
        const=False,
        help="Turn the missing-index check off for this run.",
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
        "queryguard_index_check",
        "Check each SELECT for a missing index (PostgreSQL 16 or newer)",
        type="bool",
        default=False,
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


def _read_settings(config: pytest.Config) -> _Settings:
    cli_mode = config.getoption("queryguard_mode")
    if cli_mode is not None:
        mode, source = cli_mode, "--queryguard-mode"
    else:
        source = "queryguard_mode"
        try:
            mode = config.getini(source)
        except (TypeError, ValueError) as exc:
            # pytest 9 rejects a non-string value from native TOML on its own.
            raise pytest.UsageError(
                f"{source} must be one of {', '.join(_MODES)}: {exc}"
            ) from None
    if mode not in _MODES:
        raise pytest.UsageError(
            f"{source} must be one of {', '.join(_MODES)}, got {mode!r}"
        )
    return _Settings(
        mode,
        _threshold(config, "queryguard_relationship_threshold"),
        _threshold(config, "queryguard_repeat_threshold"),
        _index_check(config),
        _ignore_tables(config),
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "max_queries(n): fail the test if it runs more than n SQL statements.",
    )
    config.addinivalue_line(
        "markers",
        "allow_nplusone: do not report N+1 query findings for this test.",
    )
    config.addinivalue_line(
        "markers",
        "allow_missing_index(*tables): skip the missing-index check for this "
        "test, or only for the given tables.",
    )
    config.stash[_outer_error] = swap_error(None)
    config.stash[_outer_stopped] = indexes.swap_stopped(None)
    settings = _read_settings(config)
    config.stash[_settings] = settings
    if config.getoption("queryguard_report"):
        config.stash[_report] = []
    if settings.mode != "off":
        install()
        if settings.index_check:
            install_index_check()


def pytest_unconfigure(config: pytest.Config) -> None:
    swap_error(config.stash.get(_outer_error, None))
    indexes.swap_stopped(config.stash.get(_outer_stopped, None))


def _report_internal_error(config: pytest.Config) -> None:
    error = internal_error()
    if error is None or config.stash.get(_internal_reported, False):
        return
    config.stash[_internal_reported] = True
    std_warnings.warn(
        QueryGuardInternalWarning(
            f"pytest-queryguard disabled itself after an internal error: {error!r}"
        ),
        stacklevel=1,
    )


def _analyse(
    item: pytest.Item, collector: QueryCollector, settings: _Settings
) -> str | None:
    """The report for the finished test, or None; never raises."""
    if internal_error() is not None or item.get_closest_marker("allow_nplusone"):
        return None
    try:
        findings = detect(
            collector,
            relationship_threshold=settings.relationship,
            repeat_threshold=settings.repeat,
        )
        if not findings:
            return None
        return format_findings(findings, cwd=item.config.rootpath)
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        record_error(exc)
        return None


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
        scans = select_findings(collector.scans, (*settings.ignore_tables, *exempt))
        if not scans:
            return None
        return format_index_findings(scans, cwd=item.config.rootpath)
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        record_error(exc)
        return None


def _note_index_check(config: pytest.Config, collector: QueryCollector) -> None:
    """Add what the index check skipped in this test to the session summary."""
    if not collector.index_check:
        return
    notes = config.stash.setdefault(_index_notes, _IndexNotes())
    notes.notices.update(collector.index_notices)
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


def _budget_report(
    item: pytest.Item, collector: QueryCollector, limit: int
) -> str | None:
    """The budget failure text, or None; a bug here never fails the test."""
    try:
        if len(collector.statements) <= limit:
            return None
        return format_budget(limit, collector.statements, cwd=item.config.rootpath)
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        record_error(exc)
        return None


def _record(item: pytest.Item, collector: QueryCollector, settings: _Settings) -> None:
    """Remember the test's statement count for the session report."""
    counts = item.config.stash.get(_report, None)
    # After an internal error the listeners stop recording, so a count would be wrong.
    if counts is None or settings.mode == "off" or internal_error() is not None:
        return
    counts.append((item.nodeid, len(collector.statements)))


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
    if notes is None or not (notes.notices or notes.errors):
        return
    terminalreporter.write_sep("=", "queryguard index check")
    for line in format_index_notes(notes.notices, notes.errors, notes.first_error):
        terminalreporter.write_line(line)


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    config = terminalreporter.config
    settings = config.stash.get(_settings, None)
    if settings is None:
        return
    try:
        _write_index_notes(terminalreporter)
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        record_error(exc)
    if not config.getoption("queryguard_report"):
        return
    try:
        _write_report(terminalreporter, settings)
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        record_error(exc)
    _report_internal_error(config)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, Any, Any]:
    settings = item.config.stash.get(_settings, None)
    if settings is None:
        # pytest_configure did not complete (for example it raised a UsageError
        # for a bad option), so there is nothing to measure with.
        return (yield)
    limit = _budget(item)
    if settings.mode == "off" and limit is None:
        return (yield)
    index_check = settings.mode != "off" and settings.index_check
    exempt_all, exempt = _index_exemption(item) if index_check else (False, ())
    if limit is not None:
        install()  # idempotent; budgets also work when the mode is off
    collector = QueryCollector()
    collector.index_check = index_check and not exempt_all
    push(collector)
    try:
        result = yield
    finally:
        pop(collector)
        _report_internal_error(item.config)
        _record(item, collector, settings)  # failing tests are reported too
        _note_index_check(item.config, collector)
    parts: list[str] = []
    if limit is not None:
        budget = _budget_report(item, collector, limit)
        if budget is not None:
            parts.append(budget)
    message = None
    if settings.mode != "off":
        found = [
            _analyse(item, collector, settings),
            _analyse_indexes(item, collector, settings, exempt),
        ]
        message = "\n\n".join(m for m in found if m is not None) or None
    _report_internal_error(item.config)
    if message is not None and (parts or settings.mode == "fail"):
        parts.append(message)  # one failure: budget first, N+1 after it
    elif message is not None:
        path, line, _ = item.reportinfo()
        std_warnings.warn_explicit(
            QueryGuardWarning(message),
            QueryGuardWarning,
            str(path),
            (line or 0) + 1,
        )
    if parts:
        pytest.fail("\n\n".join(parts), pytrace=False)
    return result
