"""Table names in the four places that take them, and the helper they share.

``queryguard_index_ignore_tables``, ``allow_missing_index``, ``queryguard_ignore``
and ``allow_nplusone(kind="missing-index")`` accept the same spellings. The
PostgreSQL tests run only when QUERYGUARD_PG_URL is set, as in
tests/test_index_check.py.
"""

from __future__ import annotations

import pytest

from pytest_queryguard.findings import KIND_MISSING_INDEX, Finding
from pytest_queryguard.ignores import IgnoreRule, apply_rules, parse_rule
from pytest_queryguard.tablenames import table_matches
from tests.test_index_check import Postgres, needs_pg, pg16, postgres  # noqa: F401

# pytester runs sessions in-process, so the outer plugin sees their queries too.
pytestmark = pytest.mark.allow_nplusone

# --- the helper ---


@pytest.mark.parametrize(
    ("pattern", "schema", "table", "expected"),
    [
        ("audit_log", None, "audit_log", True),
        ("audit_log", "public", "audit_log", True),
        ("AUDIT_LOG", "public", "audit_log", False),
        ("audit_*", None, "audit_log", True),
        ("audit_log", None, "audit_logs", False),
        ('"audit_log"', None, "audit_log", True),
        ('"AUDIT_LOG"', None, "audit_log", False),
        ("useraccount", None, "UserAccount", False),
        ("UserAccount", None, "UserAccount", True),
        ('"UserAccount"', None, "UserAccount", True),
        ('"useraccount"', None, "UserAccount", False),
        ("public.audit_log", "public", "audit_log", True),
        ("PUBLIC.Audit_Log", "public", "audit_log", False),
        ('"public"."audit_log"', "public", "audit_log", True),
        ('public."UserAccount"', "public", "UserAccount", True),
        ('"Public".audit_log', "public", "audit_log", False),
        ("public.audit_log", "other", "audit_log", False),
        ("public.audit_log", None, "audit_log", False),
        ("*.audit_log", "public", "audit_log", True),
        ("public.*", "public", "audit_log", True),
        ("public.*", "other", "audit_log", False),
        ('"my.table"', None, "my.table", True),
        ('s."my.table"', "s", "my.table", True),
        ('"a ""b"" c"', None, 'a "b" c', True),
        ('"a*"', None, "abc", False),
        ('"a*"', None, "a*", True),
        ("a.b.c", "a", "b", False),
        ('"unterminated', None, "unterminated", False),
        ("", None, "t", False),
        ("public.", "public", "t", False),
    ],
)
def test_table_matches(
    pattern: str, schema: str | None, table: str, expected: bool
) -> None:
    assert table_matches(pattern, schema, table) is expected


def test_a_pattern_that_is_not_an_identifier_still_matches_as_before() -> None:
    assert table_matches("[ab]*", None, "audit_log")
    assert table_matches('p"q', None, 'p"q')


def _index_finding(label: str, schema: str | None, table: str) -> Finding:
    return Finding(
        kind=KIND_MISSING_INDEX,
        label=label,
        file="t.py",
        function="f",
        message="m",
        table=(schema, table),
    )


@pytest.mark.parametrize(
    "label", ["audit_log", "public.audit_log", '"public"."audit_log"', "audit_*"]
)
def test_ignore_rule_matches_every_spelling_of_the_table(label: str) -> None:
    finding = _index_finding("audit_log", "public", "audit_log")
    kept, used = apply_rules([finding], [parse_rule(f"missing-index {label}")])
    assert kept == [] and len(used) == 1


@pytest.mark.parametrize("label", ["AUDIT_LOG", "public.AUDIT_LOG", '"AUDIT_LOG"'])
def test_ignore_rule_compares_table_names_case_sensitively(label: str) -> None:
    finding = _index_finding("audit_log", "public", "audit_log")
    kept, used = apply_rules([finding], [parse_rule(f"missing-index {label}")])
    assert kept == [finding] and not used


def test_a_pattern_with_a_dot_inside_a_wildcard_still_matches_as_before() -> None:
    # Before the identifier parser these matched the qualified name; they
    # must keep matching (audit* for every table in the schema audit).
    assert table_matches("audit*", "audit", "log")
    assert table_matches("pub*log", "public", "audit_log")
    assert table_matches("public[.]audit_log", "public", "audit_log")


def test_ignore_rule_still_matches_the_printed_label() -> None:
    finding = _index_finding('"UserAccount"', "public", "UserAccount")
    rule = IgnoreRule(KIND_MISSING_INDEX, '"UserAccount"')
    assert rule.matches(finding)
    assert IgnoreRule(KIND_MISSING_INDEX, "UserAccount").matches(finding)
    assert not IgnoreRule(KIND_MISSING_INDEX, '"useraccount"').matches(finding)


def test_the_table_does_not_make_other_kinds_match() -> None:
    finding = Finding(
        kind="relationship-load",
        label="Order.customer",
        file=None,
        function=None,
        message="m",
    )
    assert not IgnoreRule("*", "audit_log").matches(finding)


# --- the four places, end to end ---

TABLES = ("audit_log", "UserAccount")

# (spelling, table it is meant for, whether it matches); {schema} is the schema
# of the table, known on PostgreSQL only.
BARE = [
    ("audit_log", "audit_log", True),
    ("AUDIT_LOG", "audit_log", False),
    ('"audit_log"', "audit_log", True),
    ('"AUDIT_LOG"', "audit_log", False),
    ("audit_*", "audit_log", True),
    ("UserAccount", "UserAccount", True),
    ("useraccount", "UserAccount", False),
    ('"UserAccount"', "UserAccount", True),
    ('"useraccount"', "UserAccount", False),
    ("audit_log", "UserAccount", False),
]
QUALIFIED = [
    ("{schema}.audit_log", "audit_log", True),
    ("{schema}.AUDIT_LOG", "audit_log", False),
    ('"{schema}"."audit_log"', "audit_log", True),
    ('"{schema}".audit_log', "audit_log", True),
    ("{schema}.*", "audit_log", True),
    ("*.audit_log", "audit_log", True),
    ("{schema}.UserAccount", "UserAccount", True),
    ('{schema}."UserAccount"', "UserAccount", True),
    ('{schema}."useraccount"', "UserAccount", False),
    ("nowhere.audit_log", "audit_log", False),
    ("{schema}.audit_log", "UserAccount", False),
]

SCAN = """
import pytest
from sqlalchemy import text

from conftest_engine import make_engine

{marker}
def test_scan():
    engine = make_engine()
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE audit_log (id INTEGER PRIMARY KEY, actor INT)"))
        conn.execute(
            text('CREATE TABLE "UserAccount" (id INTEGER PRIMARY KEY, "tenantId" INT)')
        )
        {query}
    engine.dispose()
"""
QUERIES = {
    "audit_log": 'conn.execute(text("SELECT id FROM audit_log WHERE actor = 1")).all()',
    "UserAccount": (
        "conn.execute("
        'text(\'SELECT id FROM "UserAccount" WHERE "tenantId" = 1\')).all()'
    ),
}
SQLITE_ENGINE = 'from sqlalchemy import create_engine\n\ndef make_engine():\n    return create_engine("sqlite://")\n'
PG_ENGINE = """
from sqlalchemy import create_engine

def make_engine():
    return create_engine(
        {url!r}, connect_args={{"startup_params": {{"search_path": {schema!r}}}}}
    )
"""
ENTRY_POINTS = ["ini-tables", "allow_missing_index", "ini-ignore", "allow_nplusone"]


def _configure(project: pytest.Pytester, entry: str, spelling: str) -> str:
    """Set up the entry point; return the marker line for the test, if any."""
    if entry == "ini-tables":
        project.makeini(f"[pytest]\nqueryguard_index_ignore_tables =\n    {spelling}\n")
        return ""
    if entry == "ini-ignore":
        project.makeini(
            f"[pytest]\nqueryguard_ignore =\n    missing-index {spelling}\n"
        )
        return ""
    if entry == "allow_missing_index":
        return f"@pytest.mark.allow_missing_index({spelling!r})"
    return f'@pytest.mark.allow_nplusone({spelling!r}, kind="missing-index")'


def _run(
    project: pytest.Pytester,
    engine_source: str,
    entry: str,
    spelling: str,
    table: str,
    matches: bool,
) -> None:
    project.makepyfile(conftest_engine=engine_source)
    marker = _configure(project, entry, spelling)
    project.makepyfile(test_a=SCAN.format(marker=marker, query=QUERIES[table]))
    result = project.runpytest("--queryguard-index-check", "-p", "no:cacheprovider")
    if matches:
        result.assert_outcomes(passed=1)
        result.stdout.no_fnmatch_line("*matched no finding*")
    else:
        result.assert_outcomes(failed=1)


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    return pytester


@pytest.mark.parametrize("entry", ENTRY_POINTS)
@pytest.mark.parametrize(("spelling", "table", "matches"), BARE)
def test_sqlite_spellings(
    project: pytest.Pytester, entry: str, spelling: str, table: str, matches: bool
) -> None:
    _run(project, SQLITE_ENGINE, entry, spelling, table, matches)


@needs_pg
@pytest.mark.parametrize("entry", ENTRY_POINTS)
@pytest.mark.parametrize(("spelling", "table", "matches"), [*BARE, *QUALIFIED])
def test_pg_spellings(
    project: pytest.Pytester,
    pg16: Postgres,  # noqa: F811
    entry: str,
    spelling: str,
    table: str,
    matches: bool,
) -> None:
    engine = PG_ENGINE.format(url=pg16.url, schema=pg16.schema)
    _run(project, engine, entry, spelling.format(schema=pg16.schema), table, matches)
