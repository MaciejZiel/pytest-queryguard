from __future__ import annotations

import pytest

from pytest_queryguard.collector import QueryCollector
from pytest_queryguard.events import RelationshipLoadEvent, StatementEvent
from pytest_queryguard.messages import format_budget, format_truncation

# pytester runs sessions in-process, so the outer plugin sees their queries too.
pytestmark = pytest.mark.allow_nplusone

LOOP = """
import pytest
from sqlalchemy import create_engine, text


{marker}
def test_loop():
    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        for i in range(30):
            conn.execute(text(f"select {{i}} from (select 1) where 1 = 1"))
"""

SAME = """
import pytest
from sqlalchemy import create_engine, text


{marker}
def test_loop():
    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        for i in range(30):
            conn.execute(text("select 1"))
"""


def _stmt(i: int) -> StatementEvent:
    return StatementEvent(f"select {i}", None, None)


def test_collector_stores_everything_without_a_cap() -> None:
    collector = QueryCollector()
    for i in range(5):
        collector.add_statement(_stmt(i))
    assert (collector.statement_count, len(collector.statements)) == (5, 5)
    assert not collector.truncated


def test_collector_counts_past_the_cap_but_stops_storing() -> None:
    collector = QueryCollector(max_events=3)
    for i in range(10):
        collector.add_statement(_stmt(i))
    assert collector.statement_count == 10
    assert [s.sql for s in collector.statements] == ["select 0", "select 1", "select 2"]
    assert collector.truncated


def test_relationship_loads_are_capped_too() -> None:
    collector = QueryCollector(max_events=2)
    for i in range(4):
        collector.add_relationship_load(RelationshipLoadEvent("A.b", i, None))
    assert collector.relationship_load_count == 4
    assert len(collector.relationship_loads) == 2
    assert collector.truncated


def test_at_the_cap_is_not_truncated() -> None:
    collector = QueryCollector(max_events=3)
    for i in range(3):
        collector.add_statement(_stmt(i))
    assert not collector.truncated


def test_budget_message_says_how_many_were_not_stored() -> None:
    text = format_budget(2, [_stmt(1), _stmt(2)], total=7)
    assert "got 7" in text
    assert "... and 5 more, not stored (queryguard_max_events)" in text


def test_budget_message_without_total_is_unchanged() -> None:
    assert "more, not stored" not in format_budget(0, [_stmt(1)])


def test_truncation_note_names_the_option() -> None:
    assert "queryguard_max_events" in format_truncation(50)


def test_budget_counts_every_statement_past_the_cap(
    pytester: pytest.Pytester,
) -> None:
    pytester.makeini("[pytest]\nqueryguard_max_events = 5\n")
    pytester.makepyfile(test_a=LOOP.format(marker="@pytest.mark.max_queries(10)"))
    result = pytester.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        ["*expected at most 10 queries, got 30*", "*... and 25 more, not stored*"]
    )


def test_nplusone_report_notes_the_cap(pytester: pytest.Pytester) -> None:
    pytester.makeini("[pytest]\nqueryguard_max_events = 8\n")
    pytester.makepyfile(test_a=SAME.format(marker=""))
    result = pytester.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        ["*the same statement ran 8 times*", "*only the first 8 statements*"]
    )


def test_default_cap_does_not_change_reports(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(test_a=SAME.format(marker=""))
    result = pytester.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*the same statement ran 30 times*"])
    assert "queryguard_max_events" not in result.stdout.str()


def test_zero_means_no_cap(pytester: pytest.Pytester) -> None:
    pytester.makeini("[pytest]\nqueryguard_max_events = 0\n")
    pytester.makepyfile(test_a=SAME.format(marker=""))
    result = pytester.runpytest()
    result.stdout.fnmatch_lines(["*the same statement ran 30 times*"])
    assert "queryguard_max_events" not in result.stdout.str()


@pytest.mark.parametrize("value", ["-1", "many"])
def test_bad_cap_is_a_usage_error(pytester: pytest.Pytester, value: str) -> None:
    pytester.makeini(f"[pytest]\nqueryguard_max_events = {value}\n")
    result = pytester.runpytest()
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*queryguard_max_events*integer >= 0*"])


def test_report_counts_statements_past_the_cap(pytester: pytest.Pytester) -> None:
    pytester.makeini("[pytest]\nqueryguard_max_events = 5\n")
    pytester.makepyfile(test_a=LOOP.format(marker=""))
    result = pytester.runpytest("--queryguard-report")
    result.stdout.fnmatch_lines(["*   30  test_a.py::test_loop"])


def test_column_loads_are_capped_and_make_the_collector_truncated() -> None:
    from pytest_queryguard.events import ColumnLoadEvent

    collector = QueryCollector(max_events=2)
    for i in range(5):
        collector.add_column_load(
            ColumnLoadEvent("Child", ("blob",), "deferred", i, None)
        )
    assert collector.column_load_count == 5
    assert len(collector.column_loads) == 2
    assert collector.truncated
