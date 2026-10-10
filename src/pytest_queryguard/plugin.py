from __future__ import annotations

import warnings as std_warnings
from collections.abc import Generator
from typing import Any

import pytest

from .collector import QueryCollector, pop, push
from .detectors import detect
from .listeners import install, internal_error, record_error, swap_error
from .messages import format_budget, format_findings
from .warnings import QueryGuardInternalWarning, QueryGuardWarning

_MODES = ("fail", "warn", "off")
_internal_reported = pytest.StashKey[bool]()
_outer_error = pytest.StashKey[BaseException | None]()
_settings = pytest.StashKey["_Settings"]()


class _Settings:
    def __init__(self, mode: str, relationship: int, repeat: int) -> None:
        self.mode = mode
        self.relationship = relationship
        self.repeat = repeat


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


def _read_settings(config: pytest.Config) -> _Settings:
    cli_mode = config.getoption("queryguard_mode")
    if cli_mode is not None:
        mode, source = cli_mode, "--queryguard-mode"
    else:
        mode, source = config.getini("queryguard_mode"), "queryguard_mode"
    if mode not in _MODES:
        raise pytest.UsageError(
            f"{source} must be one of {', '.join(_MODES)}, got {mode!r}"
        )
    return _Settings(
        mode,
        _threshold(config, "queryguard_relationship_threshold"),
        _threshold(config, "queryguard_repeat_threshold"),
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
    config.stash[_outer_error] = swap_error(None)
    settings = _read_settings(config)
    config.stash[_settings] = settings
    if settings.mode != "off":
        install()


def pytest_unconfigure(config: pytest.Config) -> None:
    swap_error(config.stash.get(_outer_error, None))


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
    if limit is not None:
        install()  # idempotent; budgets also work when the mode is off
    collector = QueryCollector()
    push(collector)
    try:
        result = yield
    finally:
        pop(collector)
        _report_internal_error(item.config)
    parts: list[str] = []
    if limit is not None:
        budget = _budget_report(item, collector, limit)
        if budget is not None:
            parts.append(budget)
    message = None
    if settings.mode != "off":
        message = _analyse(item, collector, settings)
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
