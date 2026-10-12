from __future__ import annotations

import pytest
from sqlalchemy import Engine, create_engine, insert, select, text, update

from pytest_queryguard import NPlusOneDetected, record_queries
from pytest_queryguard.collector import QueryCollector
from pytest_queryguard.detectors import WriteLoopFinding, _value_rows, detect
from pytest_queryguard.events import CallSite, StatementEvent
from pytest_queryguard.messages import make_findings
from tests.models import Customer, make_sync_session

# pytester runs sessions in-process, so the outer plugin sees their queries too.
pytestmark = pytest.mark.allow_nplusone

SITE = CallSite(filename="app.py", lineno=10, function="save_all")
OTHER = CallSite(filename="app.py", lineno=20, function="elsewhere")
INSERT = "INSERT INTO log (msg) VALUES (?)"


def _stmts(
    sql: str, n: int, site: CallSite | None = SITE, executemany: bool = False
) -> list[StatementEvent]:
    return [
        StatementEvent(sql, site, None, parameters=(i,), executemany=executemany)
        for i in range(n)
    ]


def _loops(
    statements: list[StatementEvent], threshold: int | None = 5
) -> list[WriteLoopFinding]:
    c = QueryCollector()
    c.statements.extend(statements)
    found = detect(
        c, relationship_threshold=2, repeat_threshold=5, write_loop_threshold=threshold
    )
    return [f for f in found if isinstance(f, WriteLoopFinding)]


# ------------------------------------------------------------- the rule


def test_off_by_default() -> None:
    assert _loops(_stmts(INSERT, 50), threshold=None) == []


@pytest.mark.parametrize(
    ("sql", "verb"),
    [
        (INSERT, "INSERT"),
        ("UPDATE log SET msg = ? WHERE log.id = ?", "UPDATE"),
        ("DELETE FROM log WHERE log.id = ?", "DELETE"),
        ("  insert into log (msg) values (?)", "INSERT"),
    ],
)
def test_row_by_row_writes_are_reported(sql: str, verb: str) -> None:
    (finding,) = _loops(_stmts(sql, 6))
    assert finding.verb == verb
    assert finding.count == 6
    assert finding.first == SITE


def test_below_the_threshold_is_not_reported() -> None:
    assert _loops(_stmts(INSERT, 4)) == []
    assert len(_loops(_stmts(INSERT, 4), threshold=4)) == 1


def test_a_single_statement_is_not_reported() -> None:
    assert _loops(_stmts(INSERT, 1), threshold=2) == []


def test_executemany_is_not_a_loop() -> None:
    assert _loops(_stmts(INSERT, 20, executemany=True)) == []


def test_multi_row_values_are_already_batched() -> None:
    sql = "INSERT INTO log (msg) VALUES (?), (?), (?) RETURNING id"
    assert _loops(_stmts(sql, 20)) == []


def test_selects_are_not_write_loops() -> None:
    assert _loops(_stmts("SELECT * FROM log WHERE id = ?", 20)) == []


def test_each_call_site_is_counted_on_its_own() -> None:
    statements = _stmts(INSERT, 3) + _stmts(INSERT, 3, OTHER)
    assert _loops(statements) == []
    found = _loops(statements, threshold=3)
    assert sorted(f.first.function for f in found if f.first) == [
        "elsewhere",
        "save_all",
    ]


def test_different_statement_shapes_are_counted_separately() -> None:
    statements = _stmts(INSERT, 3) + _stmts("INSERT INTO other (a) VALUES (?)", 3)
    assert _loops(statements) == []


def test_literal_values_do_not_make_a_new_shape() -> None:
    statements = [
        StatementEvent(f"INSERT INTO log (msg) VALUES ('m{i}')", SITE, None)
        for i in range(6)
    ]
    assert len(_loops(statements)) == 1


def test_an_unknown_call_site_still_counts() -> None:
    (finding,) = _loops(_stmts(INSERT, 6, site=None))
    assert finding.first is None


@pytest.mark.parametrize(
    ("sql", "rows"),
    [
        ("INSERT INTO t (a) VALUES (?)", 1),
        ("INSERT INTO t (a) VALUES (?), (?)", 2),
        ("INSERT INTO t (a, b) VALUES (?, now()), (?, now()), (?, now())", 3),
        ("INSERT INTO t (a) VALUES ('x), (y') RETURNING id", 1),
        ("INSERT INTO t (a) VALUES ('it''s (a), (b)')", 1),
        ("INSERT INTO t (a) VALUES (coalesce(?, (1))) ON CONFLICT DO NOTHING", 1),
        ("INSERT INTO t (a) SELECT a FROM u", 1),
        ("INSERT INTO t DEFAULT VALUES", 1),
        ("INSERT INTO t (a) VALUES (?),\n (?)", 2),
    ],
)
def test_value_rows(sql: str, rows: int) -> None:
    assert _value_rows(sql) == rows


def test_the_finding_message_and_identity() -> None:
    (finding,) = make_findings(_loops(_stmts(INSERT, 6)))  # type: ignore[arg-type]
    assert finding.kind == "write-loop"
    assert finding.label == "INSERT INTO log (msg) VALUES (?)"
    assert finding.function == "save_all"
    assert finding.count == 6
    assert "the same INSERT ran 6 times, one row at a time" in finding.message
    assert "session.add_all(rows)" in finding.message
    assert "bulk insert" in finding.message


@pytest.mark.parametrize(
    ("sql", "advice"),
    [
        ("UPDATE log SET msg = ? WHERE id = ?", "update(Model)"),
        ("DELETE FROM log WHERE id = ?", "DELETE ... WHERE id IN (...)"),
    ],
)
def test_the_advice_follows_the_verb(sql: str, advice: str) -> None:
    (finding,) = make_findings(_loops(_stmts(sql, 6)))  # type: ignore[arg-type]
    assert advice in finding.message


def test_a_write_loop_is_not_a_repeated_statement_too() -> None:
    c = QueryCollector()
    c.statements.extend(_stmts(INSERT, 10))
    found = detect(
        c, relationship_threshold=2, repeat_threshold=5, write_loop_threshold=5
    )
    assert [type(f).__name__ for f in found] == ["WriteLoopFinding"]


# ------------------------------------------------- through real statements


@pytest.fixture
def engine() -> Engine:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("create table log (id integer primary key, msg text)"))
    return engine


def test_core_inserts_in_a_loop(engine: Engine) -> None:
    with engine.begin() as conn, record_queries() as log:
        for i in range(6):
            conn.execute(text("insert into log (msg) values (:m)"), {"m": str(i)})
    log.assert_no_nplusone(write_loop_threshold=7)
    with pytest.raises(NPlusOneDetected, match="the same INSERT ran 6 times"):
        log.assert_no_nplusone(write_loop_threshold=6)
    log.assert_no_nplusone()  # off by default


def test_core_executemany_and_one_statement_are_fine(engine: Engine) -> None:
    with engine.begin() as conn, record_queries() as log:
        conn.execute(
            text("insert into log (msg) values (:m)"), [{"m": str(i)} for i in range(9)]
        )
        conn.execute(text("insert into log (msg) values ('single')"))
    log.assert_no_nplusone(write_loop_threshold=2)


def test_core_updates_in_a_loop(engine: Engine) -> None:
    with engine.begin() as conn, record_queries() as log:
        for i in range(1, 7):
            conn.execute(text("update log set msg = 'x' where id = :i"), {"i": i})
    with pytest.raises(NPlusOneDetected, match="the same UPDATE ran 6 times"):
        log.assert_no_nplusone(write_loop_threshold=5)


def test_orm_add_and_flush_per_row() -> None:
    session = make_sync_session()
    with record_queries() as log:
        for i in range(6):
            session.add(Customer(id=100 + i, name=f"c{i}"))
            session.flush()
    with pytest.raises(NPlusOneDetected, match="the same INSERT ran 6 times"):
        log.assert_no_nplusone(write_loop_threshold=5)


def test_orm_add_all_and_one_flush_is_not_a_loop() -> None:
    session = make_sync_session()
    with record_queries() as log:
        session.add_all(Customer(id=100 + i, name=f"c{i}") for i in range(20))
        session.flush()
    log.assert_no_nplusone(write_loop_threshold=2)


def test_orm_bulk_insert_and_update_are_not_loops() -> None:
    session = make_sync_session()
    with record_queries() as log:
        session.execute(
            insert(Customer), [{"id": 100 + i, "name": f"c{i}"} for i in range(20)]
        )
        session.execute(
            update(Customer), [{"id": 100 + i, "name": f"d{i}"} for i in range(20)]
        )
        for customer in session.scalars(select(Customer)):
            customer.name = "changed"
        session.flush()
    log.assert_no_nplusone(write_loop_threshold=2)


@pytest.mark.parametrize("bad", [0, 1, -3, True, "5", 2.5])
def test_assert_no_nplusone_rejects_a_bad_write_loop_threshold(bad: object) -> None:
    with record_queries() as log:
        pass
    with pytest.raises(ValueError, match="write_loop_threshold"):
        log.assert_no_nplusone(write_loop_threshold=bad)  # type: ignore[arg-type]


# ------------------------------------------------------------- the plugin

BODY = """
from sqlalchemy import create_engine, text


{marker}
def test_rows():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("create table log (id integer primary key, msg text)"))
        for i in range(6):
            conn.execute(text("insert into log (msg) values (:m)"), {{"m": str(i)}})
"""


def _write(pytester: pytest.Pytester, marker: str = "") -> None:
    pytester.makepyfile(test_a=BODY.format(marker=marker))


def test_the_plugin_leaves_write_loops_alone_by_default(
    pytester: pytest.Pytester,
) -> None:
    _write(pytester)
    pytester.runpytest().assert_outcomes(passed=1)


def test_the_ini_option_turns_write_loop_detection_on(
    pytester: pytest.Pytester,
) -> None:
    _write(pytester)
    pytester.makeini("[pytest]\nqueryguard_write_loop_threshold = 5\n")
    result = pytester.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "*N+1 detected*",
            "*the same INSERT ran 6 times, one row at a time*",
            "*first at test_a.py:*in test_rows*",
            "*session.add_all(rows)*",
        ]
    )


def test_the_threshold_is_respected(pytester: pytest.Pytester) -> None:
    _write(pytester)
    pytester.makeini("[pytest]\nqueryguard_write_loop_threshold = 7\n")
    pytester.runpytest().assert_outcomes(passed=1)


@pytest.mark.parametrize("value", ["1", "x", "-5"])
def test_the_ini_option_is_validated(pytester: pytest.Pytester, value: str) -> None:
    _write(pytester)
    pytester.makeini(f"[pytest]\nqueryguard_write_loop_threshold = {value}\n")
    result = pytester.runpytest()
    assert result.ret != 0
    result.stderr.fnmatch_lines(["*queryguard_write_loop_threshold must be*"])


def test_ignore_by_kind(pytester: pytest.Pytester) -> None:
    _write(pytester)
    pytester.makeini(
        "[pytest]\nqueryguard_write_loop_threshold = 5\n"
        "queryguard_ignore = write-loop insert into log*\n"
    )
    pytester.runpytest().assert_outcomes(passed=1)


def test_the_marker_ignores_by_kind(pytester: pytest.Pytester) -> None:
    _write(pytester, '@pytest.mark.allow_nplusone(kind="write-loop")')
    pytester.makepyfile(
        test_a="import pytest\n"
        + BODY.format(marker='@pytest.mark.allow_nplusone(kind="write-loop")')
    )
    pytester.makeini("[pytest]\nqueryguard_write_loop_threshold = 5\n")
    pytester.runpytest().assert_outcomes(passed=1)


def test_fail_on_can_make_write_loops_a_warning(pytester: pytest.Pytester) -> None:
    _write(pytester)
    pytester.makeini(
        "[pytest]\nqueryguard_write_loop_threshold = 5\n"
        "queryguard_fail_on = relationship-load\n"
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=1, warnings=1)
    result.stdout.fnmatch_lines(["*the same INSERT ran 6 times*"])


def test_fail_on_can_make_write_loops_the_only_failure(
    pytester: pytest.Pytester,
) -> None:
    _write(pytester)
    pytester.makeini(
        "[pytest]\nqueryguard_write_loop_threshold = 5\n"
        "queryguard_fail_on = write-loop\n"
    )
    pytester.runpytest().assert_outcomes(failed=1)


def test_warn_mode_warns(pytester: pytest.Pytester) -> None:
    _write(pytester)
    pytester.makeini("[pytest]\nqueryguard_write_loop_threshold = 5\n")
    pytester.runpytest("--queryguard-mode=warn").assert_outcomes(passed=1, warnings=1)
