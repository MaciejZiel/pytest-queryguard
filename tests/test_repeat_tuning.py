from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from pytest_queryguard import NPlusOneDetected, record_queries
from pytest_queryguard.collector import QueryCollector
from pytest_queryguard.detectors import RepeatFinding, detect
from pytest_queryguard.events import CallSite, RelationshipLoadEvent, StatementEvent
from pytest_queryguard.messages import make_findings
from tests.models import Customer, make_sync_session

# pytester runs sessions in-process, so the outer plugin sees their queries too.
pytestmark = pytest.mark.allow_nplusone

SITE = CallSite(filename="app.py", lineno=10, function="handler")
OTHER = CallSite(filename="app.py", lineno=20, function="other")
BY_KEY = "SELECT * FROM child WHERE parent_id = ?"


def _runs(
    sql: str,
    params: list[object],
    site: CallSite | None = SITE,
    relationship: str | None = None,
) -> QueryCollector:
    c = QueryCollector()
    c.statements.extend(
        StatementEvent(sql, site, relationship, parameters=p) for p in params
    )
    return c


def _small(c: QueryCollector, small: int | None = 2) -> list[RepeatFinding]:
    found = detect(
        c,
        relationship_threshold=2,
        repeat_threshold=5,
        small_repeat_threshold=small,
    )
    assert all(isinstance(f, RepeatFinding) for f in found)
    return [f for f in found if isinstance(f, RepeatFinding)]


# ------------------------------------------------------------- the rule


@pytest.mark.parametrize("n", [2, 3, 4])
def test_small_runs_with_different_parameters_are_reported_when_enabled(
    n: int,
) -> None:
    (finding,) = _small(_runs(BY_KEY, [(i,) for i in range(n)]))
    assert finding.varying
    assert finding.count == n
    assert finding.sql == BY_KEY


@pytest.mark.parametrize("n", [2, 3, 4])
def test_small_runs_are_not_reported_by_default(n: int) -> None:
    assert _small(_runs(BY_KEY, [(i,) for i in range(n)]), small=None) == []


def test_the_default_repeat_threshold_still_decides_five_runs() -> None:
    c = _runs(BY_KEY, [(i,) for i in range(5)])
    (finding,) = _small(c, small=None)
    assert not finding.varying
    # with the small rule on, five runs are still one finding, not two
    (finding,) = _small(c)
    assert not finding.varying


def test_a_threshold_of_three_leaves_two_runs_alone() -> None:
    c = _runs(BY_KEY, [(1,), (2,)])
    assert _small(c, small=3) == []
    assert len(_small(_runs(BY_KEY, [(1,), (2,), (3,)]), small=3)) == 1


def test_the_same_parameters_again_are_not_a_loop_over_keys() -> None:
    assert _small(_runs(BY_KEY, [(1,), (1,), (1,)])) == []
    assert _small(_runs(BY_KEY, [(1,), (2,), (2,)])) == []


def test_runs_from_different_lines_are_not_a_loop() -> None:
    c = _runs(BY_KEY, [(1,), (2,)])
    c.statements.append(StatementEvent(BY_KEY, OTHER, None, parameters=(3,)))
    assert _small(c) == []


def test_runs_with_an_unknown_call_site_are_left_alone() -> None:
    assert _small(_runs(BY_KEY, [(1,), (2,), (3,)], site=None)) == []


def test_different_literals_count_as_different_parameters() -> None:
    c = QueryCollector()
    c.statements.extend(
        StatementEvent(f"SELECT * FROM child WHERE parent_id = {i}", SITE, None)
        for i in (1, 2, 3)
    )
    (finding,) = _small(c)
    assert finding.count == 3


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM child ORDER BY id LIMIT ? OFFSET ?",
        "SELECT * FROM child ORDER BY id OFFSET ? ROWS FETCH FIRST ? ROWS ONLY",
        "SELECT * FROM child WHERE id > ? ORDER BY id LIMIT ?",
        "SELECT * FROM child WHERE id >= :after ORDER BY id LIMIT :n",
        "SELECT * FROM child WHERE id < $1 ORDER BY id DESC LIMIT 10",
    ],
)
def test_pagination_is_left_alone(sql: str) -> None:
    c = _runs(sql, [(5, 0), (5, 5), (5, 10)])
    assert _small(c) == []


def test_a_limit_without_a_range_is_still_a_lookup() -> None:
    sql = "SELECT * FROM child WHERE parent_id = ? LIMIT 1"
    assert len(_small(_runs(sql, [(1,), (2,), (3,)]))) == 1


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO child (parent_id) VALUES (?)",
        "UPDATE child SET name = 'x' WHERE parent_id = ?",
        "DELETE FROM child WHERE parent_id = ?",
    ],
)
def test_writes_are_left_alone(sql: str) -> None:
    assert _small(_runs(sql, [(1,), (2,), (3,)])) == []


def test_relationship_loads_are_left_to_the_relationship_rule() -> None:
    c = _runs(BY_KEY, [(1,), (2,), (3,)], relationship="Parent.children")
    c.relationship_loads.extend(
        RelationshipLoadEvent("Parent.children", k, SITE) for k in (1,)
    )
    assert _small(c) == []


def test_a_small_threshold_at_or_above_the_repeat_threshold_does_nothing() -> None:
    c = _runs(BY_KEY, [(i,) for i in range(4)])
    assert _small(c, small=5) == []
    assert _small(c, small=9) == []


def test_the_message_says_the_parameters_differed() -> None:
    (finding,) = make_findings(
        _small(_runs(BY_KEY, [(1,), (2,), (3,)]))  # type: ignore[arg-type]
    )
    assert "the same statement ran 3 times with different parameters" in finding.message
    assert finding.kind == "repeated-statement"
    assert finding.count == 3


def test_the_regular_message_is_unchanged() -> None:
    (finding,) = make_findings(
        _small(_runs(BY_KEY, [(i,) for i in range(5)]))  # type: ignore[arg-type]
    )
    assert "the same statement ran 5 times" in finding.message
    assert "different parameters" not in finding.message


# ------------------------------------------------- through real statements


def _loop(session: Session, keys: range) -> None:
    for key in keys:
        session.scalars(select(Customer).where(Customer.id == key)).all()


def test_a_real_loop_of_three_lookups() -> None:
    session = make_sync_session()
    with record_queries() as log:
        _loop(session, range(1, 4))
    log.assert_no_nplusone()  # the default: three is a batch
    with pytest.raises(NPlusOneDetected, match="ran 3 times with different parameters"):
        log.assert_no_nplusone(small_repeat_threshold=2)
    log.assert_no_nplusone(small_repeat_threshold=4)


def test_real_pagination_is_left_alone() -> None:
    session = make_sync_session()
    with record_queries() as log:
        for page in range(3):
            session.scalars(
                select(Customer).order_by(Customer.id).limit(1).offset(page)
            ).all()
    log.assert_no_nplusone(small_repeat_threshold=2)


def test_real_text_statements_with_bound_parameters() -> None:
    engine = create_engine("sqlite://")
    with engine.connect() as conn, record_queries() as log:
        for key in (1, 2, 3):
            conn.execute(text("select :k"), {"k": key}).all()
    with pytest.raises(NPlusOneDetected, match="different parameters"):
        log.assert_no_nplusone(small_repeat_threshold=3)


def test_the_same_real_statement_with_the_same_parameters_is_not_flagged() -> None:
    engine = create_engine("sqlite://")
    with engine.connect() as conn, record_queries() as log:
        for _ in range(3):
            conn.execute(text("select :k"), {"k": 1}).all()
    log.assert_no_nplusone(small_repeat_threshold=2)


def test_executemany_is_not_kept_for_comparison() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("create table t (a integer)"))
        with record_queries() as log:
            conn.execute(
                text("insert into t values (:a)"), [{"a": i} for i in range(3)]
            )
    assert log._collector.statements[0].parameters is None


@pytest.mark.parametrize("bad", [0, 1, 5, 6, True, "2", 2.5])
def test_assert_no_nplusone_rejects_a_bad_small_threshold(bad: object) -> None:
    with record_queries() as log:
        pass
    with pytest.raises(ValueError, match="small_repeat_threshold"):
        log.assert_no_nplusone(small_repeat_threshold=bad)  # type: ignore[arg-type]


# ------------------------------------------------------------- the plugin

BODY = """
from sqlalchemy import create_engine, text


def test_loop():
    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        for key in range({n}):
            conn.execute(text("select :k"), {{"k": key}}).all()
"""

PAGES = """
from sqlalchemy import create_engine, text


def test_pages():
    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        for page in range(3):
            conn.execute(
                text("select 1 limit :n offset :o"), {"n": 5, "o": page * 5}
            ).all()
"""


def test_the_plugin_ignores_small_loops_by_default(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(test_a=BODY.format(n=3))
    pytester.runpytest().assert_outcomes(passed=1)


def test_the_ini_option_turns_the_small_rule_on(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(test_a=BODY.format(n=3))
    pytester.makeini("[pytest]\nqueryguard_small_repeat_threshold = 2\n")
    result = pytester.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*ran 3 times with different parameters*"])


def test_the_ini_option_leaves_pagination_alone(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(test_a=PAGES)
    pytester.makeini("[pytest]\nqueryguard_small_repeat_threshold = 2\n")
    pytester.runpytest().assert_outcomes(passed=1)


def test_the_small_rule_can_be_ignored_like_any_repeat(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(test_a=BODY.format(n=3))
    pytester.makeini(
        "[pytest]\nqueryguard_small_repeat_threshold = 2\n"
        "queryguard_ignore = repeated-statement select*\n"
    )
    pytester.runpytest().assert_outcomes(passed=1)


@pytest.mark.parametrize("value", ["1", "x", "-2", "5", "9"])
def test_the_ini_option_is_validated(pytester: pytest.Pytester, value: str) -> None:
    pytester.makepyfile(test_a=BODY.format(n=3))
    pytester.makeini(f"[pytest]\nqueryguard_small_repeat_threshold = {value}\n")
    result = pytester.runpytest()
    assert result.ret != 0
    result.stderr.fnmatch_lines(["*queryguard_small_repeat_threshold must be*"])


def test_the_small_threshold_is_checked_against_a_raised_repeat_threshold(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(test_a=BODY.format(n=3))
    pytester.makeini(
        "[pytest]\nqueryguard_small_repeat_threshold = 4\n"
        "queryguard_repeat_threshold = 8\n"
    )
    pytester.runpytest().assert_outcomes(passed=1)
