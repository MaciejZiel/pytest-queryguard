"""How the MySQL index check hands the statement text to PyMySQL.

PyMySQL formats the text with ``query % args`` whenever ``args`` is not None,
so a literal ``%`` only survives without arguments. These tests need no
MySQL or MariaDB server: PyMySQL's own formatting runs on a connection that
was never opened, and only the network call (``_query``) is replaced.
"""

from __future__ import annotations

import types
from typing import Any

import pytest

pymysql = pytest.importorskip("pymysql")

from pytest_queryguard import mysql_index

# pytester runs sessions in-process, so the outer plugin sees their queries too.
pytestmark = pytest.mark.allow_nplusone

FAKE_SERVER = """
import pymysql
import sqlalchemy as sa
from pymysql.cursors import Cursor

sent = []


class Recording(Cursor):
    def _query(self, query):
        sent.append(query)
        self._rows = ()
        self.rowcount = 0
        self._result = None
        return 0

    def nextset(self):
        return None


class Connection(pymysql.connections.Connection):
    _current_timeout = None
    server_status = 0

    def open(self):
        return True

    def rollback(self):
        pass

    def commit(self):
        pass

    def ping(self, reconnect=True):
        pass


def make_engine():
    fake = Connection(defer_connect=True, cursorclass=Recording)
    fake._sock = object()
    return sa.create_engine(
        "mysql+pymysql://u:p@h/db", creator=lambda: fake, _initialize=False
    )
"""


@pytest.fixture
def fake() -> Any:
    module = types.ModuleType("fakemysql")
    exec(FAKE_SERVER, module.__dict__)  # noqa: S102
    return module


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makepyfile(fakemysql=FAKE_SERVER)
    return pytester


def _connection(fake: Any) -> Any:
    connection = fake.Connection(defer_connect=True, cursorclass=fake.Recording)
    connection._sock = object()
    return connection


def test_explain_sends_a_literal_percent_when_the_execution_had_no_parameters(
    fake: Any,
) -> None:
    statement = "SELECT id FROM t WHERE name LIKE 'a%'"
    mysql_index.explain(_connection(fake), statement, {}, no_parameters=True)
    assert fake.sent == ["EXPLAIN " + statement]


def test_explain_keeps_the_formatting_the_application_got(fake: Any) -> None:
    # without no_parameters PyMySQL turns %% into %, for the statement and for
    # its EXPLAIN alike
    statement = "SELECT id FROM t WHERE name = 'a%%'"
    mysql_index.explain(_connection(fake), statement, {})
    assert fake.sent == ["EXPLAIN SELECT id FROM t WHERE name = 'a%'"]


def test_explain_formats_real_parameters(fake: Any) -> None:
    statement = "SELECT id FROM t WHERE name LIKE %s"
    mysql_index.explain(_connection(fake), statement, ("a%",))
    assert fake.sent == ["EXPLAIN SELECT id FROM t WHERE name LIKE 'a%'"]


def test_explain_without_no_parameters_fails_on_a_single_percent(fake: Any) -> None:
    # the application's own execution fails the same way, so nothing to match
    statement = "SELECT id FROM t WHERE name LIKE 'a%'"
    with pytest.raises(mysql_index._ExplainFailed, match="format"):
        mysql_index.explain(_connection(fake), statement, {})


TEST_BODY = """
from fakemysql import make_engine, sent


def test_literal_percent():
    engine = make_engine()
    with engine.connect() as conn:
        conn = conn.execution_options(no_parameters=True)
        conn.exec_driver_sql("SELECT 1 FROM t WHERE n LIKE 'a%'")
    assert sent[0] == "SELECT 1 FROM t WHERE n LIKE 'a%'"
    assert "EXPLAIN SELECT 1 FROM t WHERE n LIKE 'a%'" in sent
"""


def test_a_statement_run_with_no_parameters_is_explained_without_error(
    project: pytest.Pytester,
) -> None:
    project.makepyfile(test_a=TEST_BODY)
    project.syspathinsert()
    result = project.runpytest("--queryguard-index-check", "-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)
    result.stdout.no_fnmatch_line("*could not be explained*")
