"""A snapshot of everything users can depend on.

If one of these tests fails, a public name, signature, option or default
changed. That is a breaking change unless it is a pure addition: follow
docs/stability.md (deprecate first, remove in a major release), and update
the snapshot here in the same commit so the change is visible in review.
Additions are allowed but must be added to the snapshot on purpose.
"""

from __future__ import annotations

import dataclasses
import importlib.metadata
import inspect
import re
from typing import Any

import pytest

import pytest_queryguard

EXPECTED_ALL = [
    "CallSite",
    "NPlusOneDetected",
    "Query",
    "QueryBudgetExceeded",
    "QueryGuardInternalWarning",
    "QueryGuardWarning",
    "QueryLog",
    "__version__",
    "max_db_time",
    "max_queries",
    "record_queries",
]

EXPECTED_FUNCTIONS = {
    "max_db_time": "(seconds: 'float') -> 'AbstractContextManager[QueryCollector]'",
    "max_queries": "(limit: 'int') -> 'AbstractContextManager[QueryCollector]'",
    "record_queries": "() -> 'AbstractContextManager[QueryLog]'",
}

EXPECTED_BASES = {
    "CallSite": "object",
    "NPlusOneDetected": "AssertionError",
    "Query": "object",
    "QueryBudgetExceeded": "AssertionError",
    "QueryGuardInternalWarning": "UserWarning",
    "QueryGuardWarning": "UserWarning",
    "QueryLog": "object",
}

EXPECTED_DATACLASS_FIELDS = {
    "CallSite": [("filename", "str"), ("lineno", "int"), ("function", "str")],
    "Query": [
        ("sql", "str"),
        ("callsite", "CallSite | None"),
        ("relationship", "str | None"),
        ("duration", "float | None"),
    ],
}

# Public members of QueryLog (names without a leading underscore, plus the
# container protocol), with their signatures.
EXPECTED_QUERYLOG = {
    "__getitem__": "(self, index: 'int | slice') -> 'Query | tuple[Query, ...]'",
    "__iter__": "(self) -> 'Iterator[Query]'",
    "__len__": "(self) -> 'int'",
    "assert_no_nplusone": (
        "(self, *, relationship_threshold: 'int' = 2, repeat_threshold: 'int' = 5, "
        "column_load_threshold: 'int' = 5, "
        "small_repeat_threshold: 'int | None' = None, "
        "write_loop_threshold: 'int | None' = None) -> 'None'"
    ),
    "count": "property",
    "db_time": "property",
    "queries": "property",
    "slowest": "(self, n: 'int' = 5) -> 'Query | tuple[Query, ...]'",
}

EXPECTED_INI = {
    "queryguard_mode": "fail",
    "queryguard_relationship_threshold": "2",
    "queryguard_repeat_threshold": "5",
    "queryguard_column_load_threshold": "5",
    "queryguard_max_events": "10000",
    "queryguard_small_repeat_threshold": "0",
    "queryguard_write_loop_threshold": "0",
    "queryguard_unused_eager_load": "off",
    "queryguard_fail_on": "[]",
    "queryguard_ignore": "[]",
    "queryguard_baseline": "",
    "queryguard_index_check": "False",
    "queryguard_index_ignore_tables": "[]",
}

EXPECTED_FLAGS = [
    "--no-queryguard-index-check",
    "--queryguard-baseline",
    "--queryguard-index-check",
    "--queryguard-mode",
    "--queryguard-report",
    "--queryguard-update-baseline",
]

EXPECTED_MARKERS = [
    "allow_missing_index",
    "allow_nplusone",
    "max_db_time",
    "max_queries",
]


def test_all_is_the_documented_list() -> None:
    assert sorted(pytest_queryguard.__all__) == EXPECTED_ALL
    for name in pytest_queryguard.__all__:
        assert hasattr(pytest_queryguard, name), name


def test_version_is_a_plain_release_number() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", pytest_queryguard.__version__)


@pytest.mark.parametrize("name", sorted(EXPECTED_FUNCTIONS))
def test_function_signatures(name: str) -> None:
    func = getattr(pytest_queryguard, name)
    assert str(inspect.signature(func)) == EXPECTED_FUNCTIONS[name]


@pytest.mark.parametrize("name", sorted(EXPECTED_BASES))
def test_class_bases(name: str) -> None:
    cls = getattr(pytest_queryguard, name)
    assert [base.__name__ for base in cls.__bases__] == [EXPECTED_BASES[name]]


@pytest.mark.parametrize("name", sorted(EXPECTED_DATACLASS_FIELDS))
def test_dataclass_fields(name: str) -> None:
    cls = getattr(pytest_queryguard, name)
    fields = [(f.name, str(f.type)) for f in dataclasses.fields(cls)]
    assert fields == EXPECTED_DATACLASS_FIELDS[name]
    assert cls.__dataclass_params__.frozen  # type: ignore[attr-defined]


def test_query_constructor_is_keyword_only() -> None:
    params = inspect.signature(pytest_queryguard.Query).parameters
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in params.values())


def _public_members(cls: type) -> dict[str, str]:
    members: dict[str, str] = {}
    for name, value in vars(cls).items():
        if name.startswith("_") and name not in ("__getitem__", "__iter__", "__len__"):
            continue
        if isinstance(value, property):
            members[name] = "property"
        elif callable(value):
            members[name] = str(inspect.signature(value))
    return members


def test_querylog_public_members() -> None:
    members = _public_members(pytest_queryguard.QueryLog)
    # __getitem__ is overloaded: the implementation signature is the stable one.
    assert members.pop("slowest").startswith("(self, n: 'int' = 5)")
    expected = dict(EXPECTED_QUERYLOG)
    expected.pop("slowest")
    assert members == expected


def _help_lines(pytester: pytest.Pytester, *args: str) -> str:
    return pytester.runpytest_subprocess(*args).stdout.str()


def test_ini_option_names_and_defaults(pytester: pytest.Pytester) -> None:
    config = pytester.parseconfig()
    defaults: dict[str, Any] = {}
    for name in sorted(EXPECTED_INI):
        defaults[name] = str(config.getini(name))
    assert defaults == EXPECTED_INI


def test_no_unlisted_ini_options(pytester: pytest.Pytester) -> None:
    text = _help_lines(pytester, "--help")
    found = sorted(set(re.findall(r"^\s+(queryguard_\w+) \(", text, re.MULTILINE)))
    assert found == sorted(EXPECTED_INI)


def test_command_line_flags(pytester: pytest.Pytester) -> None:
    text = _help_lines(pytester, "--help")
    found = sorted(set(re.findall(r"(--(?:no-)?queryguard-[\w-]+)", text)))
    assert found == EXPECTED_FLAGS


def test_markers(pytester: pytest.Pytester) -> None:
    text = _help_lines(pytester, "--markers")
    found = sorted(
        {
            name
            for name in re.findall(r"@pytest\.mark\.(\w+)", text)
            if name in EXPECTED_MARKERS or name.startswith(("allow_", "max_"))
        }
    )
    assert found == EXPECTED_MARKERS


def test_entry_point_name() -> None:
    points = importlib.metadata.entry_points(group="pytest11")
    ours = [p for p in points if p.value.startswith("pytest_queryguard")]
    assert [(p.name, p.value) for p in ours] == [
        ("queryguard", "pytest_queryguard.plugin")
    ]
