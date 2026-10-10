from __future__ import annotations

import warnings as std_warnings
from collections.abc import Generator
from typing import Any

import pytest

from .collector import QueryCollector, pop, push
from .detectors import detect
from .listeners import install, internal_error
from .messages import format_findings
from .warnings import QueryGuardInternalWarning, QueryGuardWarning

_MODES = ("fail", "warn", "off")
_internal_reported = pytest.StashKey[bool]()
_settings = pytest.StashKey["_Settings"]()


class _Settings:
    def __init__(self, mode: str, relationship: int, repeat: int) -> None:
        self.mode = mode
        self.relationship = relationship
        self.repeat = repeat


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
        default="2",
    )
    parser.addini(
        "queryguard_repeat_threshold",
        "Executions of one statement treated as N+1 (integer >= 2)",
        default="5",
    )


def _threshold(config: pytest.Config, name: str) -> int:
    raw = config.getini(name)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 0
    if value < 2:
        raise pytest.UsageError(f"{name} must be an integer >= 2, got {raw!r}")
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
    settings = _read_settings(config)
    config.stash[_settings] = settings
    if settings.mode != "off":
        install()


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


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, Any, Any]:
    settings = item.config.stash.get(_settings, None)
    if settings is None or settings.mode == "off":
        return (yield)
    collector = QueryCollector()
    push(collector)
    try:
        result = yield
    finally:
        pop(collector)
    _report_internal_error(item.config)
    if item.get_closest_marker("allow_nplusone") is None:
        findings = detect(
            collector,
            relationship_threshold=settings.relationship,
            repeat_threshold=settings.repeat,
        )
        if findings:
            message = format_findings(findings, cwd=item.config.rootpath)
            if settings.mode == "fail":
                pytest.fail(message, pytrace=False)
            std_warnings.warn(QueryGuardWarning(message), stacklevel=1)
    return result
