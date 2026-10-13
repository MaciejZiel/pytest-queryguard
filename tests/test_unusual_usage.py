"""Unusual ways to use SQLAlchemy: the plugin must count right or stay out.

Each scenario runs in a pytest session of its own with every check on (index
check, unused eager loads, a write-loop threshold, SARIF), and asserts on the
statements ``record_queries()`` saw. No scenario may produce an internal error.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# pytester runs sessions in-process, so the outer plugin sees their queries too.
pytestmark = pytest.mark.allow_nplusone

MODELS = (Path(__file__).parent / "models.py").read_text()

HEAD = """
import pytest
from unittest import mock
from sqlalchemy import create_engine, event, insert, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool, StaticPool

from pytest_queryguard import listeners, record_queries
from qgmodels import Base, Customer, Order, make_sync_session


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()
"""

SCENARIOS = {
    "raw_dbapi_connection": """
def test_it(engine):
    # Not seen: nothing goes through SQLAlchemy's events (documented).
    with record_queries() as log:
        raw = engine.raw_connection()
        try:
            cursor = raw.cursor()
            cursor.execute("SELECT 1")
            cursor.executemany("INSERT INTO customers (id, name) VALUES (?, ?)",
                               [(i, "x") for i in range(10, 20)])
            raw.commit()
        finally:
            raw.close()
    assert log.count == 0
""",
    "exec_driver_sql": """
def test_it(engine):
    with engine.begin() as conn, record_queries() as log:
        conn.exec_driver_sql("INSERT INTO customers (id, name) VALUES (?, ?)", (1, "a"))
        conn.exec_driver_sql("SELECT * FROM customers WHERE name = ?", ("a",)).all()
    assert [q.sql.split()[0] for q in log] == ["INSERT", "SELECT"]
""",
    "executemany": """
def test_it(engine):
    rows = [{"id": i, "name": f"c{i}"} for i in range(50)]
    with engine.begin() as conn, record_queries() as log:
        conn.execute(text("INSERT INTO customers (id, name) VALUES (:id, :name)"), rows)
    assert log.count == 1  # one executemany, not 50 statements
""",
    "insertmanyvalues": """
def test_it(engine):
    rows = [{"id": i, "name": f"c{i}"} for i in range(50)]
    with Session(engine) as session, record_queries() as log:
        ids = session.scalars(insert(Customer).returning(Customer.id), rows).all()
        session.commit()
    assert sorted(ids) == list(range(50))
    assert 1 <= log.count <= 3  # batched, whatever the SQLAlchemy version
""",
    "no_autoflush": """
def test_it():
    session = make_sync_session()
    with session.no_autoflush, record_queries() as log:
        session.add(Customer(id=99, name="pending"))
        # no autoflush: the pending row is not written, so not found
        found = session.scalars(select(Customer).where(Customer.id == 99)).first()
        assert found is None
        for order in session.scalars(select(Order).where(Order.id < 12)).all():
            _ = order.customer.name
    assert not any(q.sql.startswith("INSERT") for q in log)
    assert log.count >= 3
    session.rollback()
""",
    "several_statements_in_one_text": """
import sqlite3


def test_it(engine):
    # SQLite refuses it; the user's error must come through unchanged.
    with Session(engine) as session, record_queries() as log:
        with pytest.raises(Exception) as raised:
            session.execute(text("SELECT 1; SELECT 2"))
    assert "one statement at a time" in str(raised.value)
    assert log.count == 1
    assert log[0].duration is None  # it raised
""",
    "mock_engine": """
def test_it():
    # A mock in place of the engine never reaches the events.
    fake = mock.MagicMock(spec=Engine)
    with record_queries() as log:
        with fake.connect() as conn:
            conn.execute(text("SELECT 1"))
        session = Session(bind=mock.MagicMock())
        session.execute = mock.MagicMock(return_value=[])
        session.execute(select(Customer))
    assert log.count == 0
""",
    "null_pool": """
def test_it(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'n.sqlite'}", poolclass=NullPool)
    Base.metadata.create_all(engine)
    with record_queries() as log:
        for i in range(3):
            with engine.begin() as conn:  # a new DB-API connection each time
                conn.execute(select(Customer).where(Customer.name == str(i))).all()
    engine.dispose()
    assert log.count == 3
""",
    "static_pool": """
def test_it():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with record_queries() as log:
        with engine.connect() as a, engine.connect() as b:
            a.execute(text("SELECT 1"))
            b.execute(text("SELECT 2"))
    engine.dispose()
    assert log.count == 2
""",
    "user_events_added_and_removed": """
def test_it(engine):
    seen = []

    def mine(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(Engine, "before_cursor_execute", mine)
    try:
        with engine.connect() as conn, record_queries() as log:
            conn.execute(text("SELECT 1"))
            event.remove(Engine, "before_cursor_execute", mine)
            conn.execute(text("SELECT 2"))
    finally:
        if event.contains(Engine, "before_cursor_execute", mine):
            event.remove(Engine, "before_cursor_execute", mine)
    assert seen == ["SELECT 1"]
    assert log.count == 2
""",
    "engine_level_events_cleared": """
def test_it(engine):
    # Listeners on the engine object itself; the plugin's are on the Engine class.
    event.listen(engine, "before_cursor_execute", lambda *args: None)
    engine.dispatch._clear()
    with engine.connect() as conn, record_queries() as log:
        conn.execute(text("SELECT 1"))
    assert log.count == 1
""",
}


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makepyfile(qgmodels=MODELS)
    return pytester


ARGS = (
    "-p",
    "no:randomly",
    "--queryguard-index-check",
    "--queryguard-sarif=out.sarif",
    "-o",
    "queryguard_unused_eager_load=warn",
    "-o",
    "queryguard_write_loop_threshold=2",
    "-W",
    "error::pytest_queryguard.QueryGuardInternalWarning",
)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_unusual_usage_is_counted_or_left_alone(
    project: pytest.Pytester, name: str
) -> None:
    project.makepyfile(**{f"test_{name}": HEAD + SCENARIOS[name]})
    result = project.runpytest(*ARGS, "--queryguard-mode=warn")
    output = result.stdout.str()
    assert "INTERNALERROR" not in output
    assert "measurement stopped" not in output, output[-3000:]
    assert "QueryGuardInternalWarning" not in output, output[-3000:]
    result.assert_outcomes(passed=1, warnings=result.parseoutcomes().get("warnings", 0))


def test_removing_the_plugins_listener_stops_counting_silently(
    project: pytest.Pytester,
) -> None:
    # Documented limit: a suite that removes the plugin's own class-level
    # listener turns measurement off without a warning.
    project.makepyfile(
        test_removed=HEAD
        + """
@pytest.mark.max_queries(0)
def test_it(engine):
    event.remove(Engine, "before_cursor_execute", listeners._on_cursor_execute)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    finally:
        event.listen(Engine, "before_cursor_execute", listeners._on_cursor_execute)
"""
    )
    project.runpytest("-p", "no:randomly").assert_outcomes(passed=1)
