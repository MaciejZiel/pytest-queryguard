"""The missing-index check, end to end.

Tests that need PostgreSQL run only when QUERYGUARD_PG_URL is set, for
example ``postgresql+pg8000://postgres:pg@localhost:5432/postgres``.
"""

from __future__ import annotations

import os
import textwrap
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

# pytester runs sessions in-process, so the outer plugin sees their queries too.
pytestmark = pytest.mark.allow_nplusone

MODELS = (Path(__file__).parent / "models.py").read_text()

# A statement from a dialect the check does not support. The tables are SQLite's,
# but the connection reports another dialect name (SQLite itself is checked, see
# tests/test_sqlite_index.py).
SQLITE_TEST = """
from qgmodels import Order, make_sync_session


def test_sqlite():
    session = make_sync_session()
    session.get_bind().dialect.name = "oracle"
    assert session.query(Order).filter(Order.customer_id == 1).count() == 2
"""

SQLITE_NOTICE = (
    "oracle statements were not checked: the index check needs PostgreSQL 16 or newer"
)


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makepyfile(qgmodels=MODELS)
    return pytester


# ------------------------------------------------------------ without a database


def test_check_is_off_by_default(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=SQLITE_TEST)
    result = project.runpytest()
    result.assert_outcomes(passed=1)
    result.stdout.no_fnmatch_line("*queryguard index check*")


def test_cli_flag_turns_the_check_on_and_sqlite_gets_one_notice(
    project: pytest.Pytester,
) -> None:
    project.makepyfile(
        test_a=SQLITE_TEST + SQLITE_TEST.replace("def test_sqlite", "def test_again")
    )
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(
        ["*= queryguard index check =*", SQLITE_NOTICE, "*2 passed*"]
    )
    assert result.stdout.str().count(SQLITE_NOTICE) == 1


def test_ini_option_turns_the_check_on(project: pytest.Pytester) -> None:
    project.makeini("[pytest]\nqueryguard_index_check = true\n")
    project.makepyfile(test_a=SQLITE_TEST)
    result = project.runpytest()
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines([SQLITE_NOTICE])


@pytest.mark.skipif(
    pytest.version_tuple < (9,), reason="native TOML config needs pytest 9"
)
def test_native_toml_bool_turns_the_check_on(project: pytest.Pytester) -> None:
    project.makepyprojecttoml(
        "[tool.pytest]\n"
        "queryguard_index_check = true\n"
        'queryguard_index_ignore_tables = ["countries"]\n'
    )
    project.makepyfile(test_a=SQLITE_TEST)
    result = project.runpytest()
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines([SQLITE_NOTICE])


def test_no_flag_turns_the_check_off_for_one_run(project: pytest.Pytester) -> None:
    project.makeini("[pytest]\nqueryguard_index_check = true\n")
    project.makepyfile(test_a=SQLITE_TEST)
    result = project.runpytest("--no-queryguard-index-check")
    result.assert_outcomes(passed=1)
    result.stdout.no_fnmatch_line("*queryguard index check*")


def test_last_index_check_flag_wins(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=SQLITE_TEST)
    result = project.runpytest(
        "--no-queryguard-index-check", "--queryguard-index-check"
    )
    result.stdout.fnmatch_lines([SQLITE_NOTICE])


def test_stopped_check_does_not_leak_between_sessions(
    project: pytest.Pytester,
) -> None:
    from pytest_queryguard import indexes

    old = indexes.swap_stopped("stopped in the outer session")
    try:
        project.makepyfile(test_a=SQLITE_TEST)
        result = project.runpytest("--queryguard-index-check")
        result.stdout.fnmatch_lines([SQLITE_NOTICE])
        result.stdout.no_fnmatch_line("*stopped in the outer session*")
        assert indexes.swap_stopped(old) == "stopped in the outer session"
    except BaseException:
        indexes.swap_stopped(old)
        raise


def test_missing_plpgsql_flag_does_not_leak_between_sessions(
    project: pytest.Pytester,
) -> None:
    from pytest_queryguard import indexes

    class Engine:
        pass

    engine = Engine()
    marked = indexes.EngineSet()
    marked.add(engine)
    assert engine not in indexes.swap_no_plpgsql(marked)
    try:
        project.makepyfile(
            test_a="""
            from pytest_queryguard import indexes

            def test_starts_clean():
                assert not indexes.swap_no_plpgsql(indexes.EngineSet())
            """
        )
        project.runpytest("--queryguard-index-check").assert_outcomes(passed=1)
        assert indexes.swap_no_plpgsql(indexes.EngineSet()) is marked
    except BaseException:
        indexes.swap_no_plpgsql(indexes.EngineSet())
        raise


def test_off_mode_turns_the_check_off(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=SQLITE_TEST)
    result = project.runpytest("--queryguard-index-check", "--queryguard-mode=off")
    result.assert_outcomes(passed=1)
    result.stdout.no_fnmatch_line("*queryguard index check*")


def test_invalid_ini_bool_is_usage_error(project: pytest.Pytester) -> None:
    project.makeini("[pytest]\nqueryguard_index_check = maybe\n")
    result = project.runpytest()
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*queryguard_index_check must be true or false*"])
    result.stderr.no_fnmatch_line("*INTERNALERROR*")


@pytest.mark.skipif(
    pytest.version_tuple < (9,), reason="native TOML config needs pytest 9"
)
@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("queryguard_index_check = 3", "queryguard_index_check must be true or false"),
        (
            "queryguard_index_ignore_tables = 3",
            "queryguard_index_ignore_tables must be a list of table names",
        ),
        (
            'queryguard_index_ignore_tables = ["a", 3]',
            "queryguard_index_ignore_tables must be a list of table names",
        ),
    ],
)
def test_wrongly_typed_native_toml_is_usage_error(
    project: pytest.Pytester, line: str, message: str
) -> None:
    project.makepyprojecttoml(f"[tool.pytest]\n{line}\n")
    result = project.runpytest()
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines([f"*{message}*"])
    result.stderr.no_fnmatch_line("*INTERNALERROR*")


def test_allow_missing_index_marker_is_registered(project: pytest.Pytester) -> None:
    project.makepyfile(
        test_a="""
        import pytest


        @pytest.mark.allow_missing_index
        def test_a():
            pass


        @pytest.mark.allow_missing_index("countries", "audit.*")
        def test_b():
            pass
        """
    )
    project.runpytest("--strict-markers").assert_outcomes(passed=2)


@pytest.mark.parametrize("args", ["3", "'countries', None", "table='countries'", "''"])
def test_allow_missing_index_marker_needs_table_names(
    project: pytest.Pytester, args: str
) -> None:
    project.makepyfile(
        test_a=f"""
        import pytest


        @pytest.mark.allow_missing_index({args})
        def test_a():
            pass
        """
    )
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        ["*allow_missing_index on test_a.py::test_a takes table names*"]
    )


def test_bad_marker_is_ignored_when_the_check_is_off(project: pytest.Pytester) -> None:
    project.makepyfile(
        test_a="""
        import pytest


        @pytest.mark.allow_missing_index(3)
        def test_a():
            pass
        """
    )
    project.runpytest().assert_outcomes(passed=1)


def test_check_error_disables_the_plugin_without_failing(
    project: pytest.Pytester,
) -> None:
    project.makeconftest(
        """
        import pytest
        from pytest_queryguard import indexes


        @pytest.fixture(autouse=True)
        def broken_check(monkeypatch):
            def boom(*args, **kwargs):
                raise RuntimeError("check broke")

            monkeypatch.setattr(indexes, "check", boom)
        """
    )
    project.makepyfile(test_a=SQLITE_TEST)
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(passed=1, warnings=1)
    result.stdout.fnmatch_lines(["*QueryGuardInternalWarning*check broke*"])


def test_index_analysis_error_does_not_fail_test(
    project: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pytest_queryguard import plugin
    from pytest_queryguard.collector import QueryCollector
    from pytest_queryguard.events import ScanEvent, ScanIssue

    issue = ScanIssue("public", "t", "Seq Scan", "filter", "(t.a = $1)", "a")
    real_collector = QueryCollector

    def collector_with_scan() -> QueryCollector:
        collector = real_collector()
        collector.scans.append(ScanEvent(issue, "SELECT 1", None))
        return collector

    def boom(*args: object, **kwargs: object) -> None:
        raise ValueError("selection broke")

    monkeypatch.setattr(plugin, "QueryCollector", collector_with_scan)
    monkeypatch.setattr(plugin, "select_findings", boom)
    project.makepyfile(test_a=SQLITE_TEST)
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(passed=1, warnings=1)
    result.stdout.fnmatch_lines(["*QueryGuardInternalWarning*selection broke*"])


# ---------------------------------------------------------------- PostgreSQL

PG_URL = os.environ.get("QUERYGUARD_PG_URL")
needs_pg = pytest.mark.skipif(not PG_URL, reason="QUERYGUARD_PG_URL is not set")


@dataclass(frozen=True)
class Postgres:
    url: str
    schema: str
    version: tuple[int, ...]


@pytest.fixture
def postgres() -> Iterator[Postgres]:
    """A fresh schema in the test database, dropped afterwards."""
    from sqlalchemy import create_engine, text

    assert PG_URL is not None
    schema = f"qg_{uuid.uuid4().hex[:12]}"
    engine = create_engine(PG_URL)
    try:
        with engine.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA {schema}"))
        version = engine.dialect.server_version_info
        assert version is not None
        yield Postgres(PG_URL, schema, tuple(version))
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL lock_timeout = '5s'"))
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
    finally:
        engine.dispose()


@pytest.fixture
def pg16(postgres: Postgres) -> Postgres:
    if postgres.version < (16,):
        pytest.skip("the index check needs PostgreSQL 16 or newer")
    return postgres


# The inner projects get a `session` fixture on a fresh engine whose
# search_path is the test's own schema. orders.customer_id has no index.
PG_CONFTEST = """
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from qgmodels import Base, Customer, Order

URL = {url!r}
SCHEMA = {schema!r}


def make_engine(**kwargs):
    return create_engine(
        URL, connect_args={{"startup_params": {{"search_path": SCHEMA}}}}, **kwargs
    )


@pytest.fixture
def engine():
    engine = make_engine()
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for c in range(1, 4):
            customer = Customer(id=c, name=f"customer {{c}}")
            customer.orders = [Order(id=c * 10 + o) for o in range(2)]
            session.add(customer)
        session.commit()
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as session:
        yield session
"""


def _pg_project(project: pytest.Pytester, pg: Postgres) -> pytest.Pytester:
    project.makeconftest(PG_CONFTEST.format(url=pg.url, schema=pg.schema))
    return project


ORDERS_OF_CUSTOMER = """
from sqlalchemy import select

from qgmodels import Order


def test_orders(session):
    stmt = select(Order).where(Order.customer_id == 1)
    assert len(session.scalars(stmt).all()) == 2  # QG_LINE
"""


def _line(path: Path) -> int:
    """1-based line of the single `# QG_LINE` marker in a generated file."""
    lines = path.read_text().splitlines()
    [number] = [n for n, text in enumerate(lines, start=1) if "# QG_LINE" in text]
    return number


@needs_pg
def test_pg_seq_scan_fails_with_table_condition_sql_site_and_hint(
    project: pytest.Pytester, pg16: Postgres
) -> None:
    path = _pg_project(project, pg16).makepyfile(test_a=ORDERS_OF_CUSTOMER)
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "*queryguard: missing index",
            "",
            "  orders is read in full: Seq Scan with filter (orders.customer_id = $1)",
            f"  first at test_a.py:{_line(path)} in test_orders",
            (
                "  sql: SELECT orders.id, orders.customer_id FROM orders "
                "WHERE orders.customer_id = %s*"
            ),
            "  fix: CREATE INDEX ON orders (customer_id)",
        ]
    )


@needs_pg
def test_pg_passes_once_the_index_exists(
    project: pytest.Pytester, pg16: Postgres
) -> None:
    _pg_project(project, pg16).makepyfile(
        test_a=ORDERS_OF_CUSTOMER.replace(
            "def test_orders(session):",
            "def test_orders(session):\n"
            "    from sqlalchemy import text\n"
            '    session.execute(text("CREATE INDEX ON orders (customer_id)"))',
        )
    )
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(passed=1)


@needs_pg
def test_pg_check_is_off_without_the_option(
    project: pytest.Pytester, pg16: Postgres
) -> None:
    _pg_project(project, pg16).makepyfile(test_a=ORDERS_OF_CUSTOMER)
    project.runpytest().assert_outcomes(passed=1)


@needs_pg
def test_pg_warn_mode_warns(project: pytest.Pytester, pg16: Postgres) -> None:
    _pg_project(project, pg16).makepyfile(test_a=ORDERS_OF_CUSTOMER)
    result = project.runpytest("--queryguard-index-check", "--queryguard-mode=warn")
    result.assert_outcomes(passed=1, warnings=1)
    result.stdout.fnmatch_lines(["*QueryGuardWarning: queryguard: missing index*"])


@needs_pg
@pytest.mark.parametrize("pattern", ["orders", "ord*", "{schema}.orders"])
def test_pg_ignored_tables_pass(
    project: pytest.Pytester, pg16: Postgres, pattern: str
) -> None:
    _pg_project(project, pg16).makepyfile(test_a=ORDERS_OF_CUSTOMER)
    project.makeini(
        "[pytest]\n"
        "queryguard_index_ignore_tables =\n"
        "    countries\n"
        f"    {pattern.format(schema=pg16.schema)}\n"
    )
    project.runpytest("--queryguard-index-check").assert_outcomes(passed=1)


@needs_pg
@pytest.mark.parametrize(
    ("marker", "outcome"),
    [
        ("@pytest.mark.allow_missing_index", "passed"),
        ('@pytest.mark.allow_missing_index("orders")', "passed"),
        ('@pytest.mark.allow_missing_index("customers")', "failed"),
    ],
)
def test_pg_allow_missing_index_marker(
    project: pytest.Pytester, pg16: Postgres, marker: str, outcome: str
) -> None:
    source = "import pytest\n" + ORDERS_OF_CUSTOMER.replace(
        "def test_orders", f"{marker}\ndef test_orders"
    )
    _pg_project(project, pg16).makepyfile(test_a=source)
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(**{outcome: 1})


@needs_pg
def test_pg_allow_nplusone_does_not_turn_off_the_index_check(
    project: pytest.Pytester, pg16: Postgres
) -> None:
    source = "import pytest\n" + ORDERS_OF_CUSTOMER.replace(
        "def test_orders", "@pytest.mark.allow_nplusone\ndef test_orders"
    )
    _pg_project(project, pg16).makepyfile(test_a=source)
    project.runpytest("--queryguard-index-check").assert_outcomes(failed=1)


@needs_pg
def test_pg_joinedload_reports_the_joined_table(
    project: pytest.Pytester, pg16: Postgres
) -> None:
    _pg_project(project, pg16).makepyfile(
        test_a="""
        from sqlalchemy import select
        from sqlalchemy.orm import joinedload

        from qgmodels import Customer


        def test_customer(session):
            stmt = (
                select(Customer)
                .options(joinedload(Customer.orders))
                .where(Customer.id == 1)
            )
            assert len(session.scalars(stmt).unique().one().orders) == 2
        """
    )
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            (
                "  orders is read in full: Seq Scan with filter "
                "(orders_1.customer_id = $1)"
            ),
            "  fix: CREATE INDEX ON orders (customer_id)",
        ]
    )


@needs_pg
def test_pg_primary_key_order_scan_is_reported(
    project: pytest.Pytester, pg16: Postgres
) -> None:
    _pg_project(project, pg16).makepyfile(
        test_a="""
        from sqlalchemy import select

        from qgmodels import Order


        def test_latest(session):
            stmt = (
                select(Order)
                .where(Order.customer_id == 1)
                .order_by(Order.id)
                .limit(10)
            )
            assert len(session.scalars(stmt).all()) == 2
        """
    )
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            (
                "  orders is read in full: Index Scan using orders_pkey with filter "
                "(orders.customer_id = $1)"
            ),
        ]
    )


@needs_pg
def test_pg_lookups_by_primary_key_and_full_reads_pass(
    project: pytest.Pytester, pg16: Postgres
) -> None:
    _pg_project(project, pg16).makepyfile(
        test_a="""
        from sqlalchemy import func, select

        from qgmodels import Customer, Order


        def test_ok(session):
            assert session.get(Customer, 1).name == "customer 1"
            assert len(session.scalars(select(Order)).all()) == 6
            assert session.scalar(select(func.count()).select_from(Order)) == 6
            ids = [1, 2, 3]
            stmt = select(Customer).where(Customer.id.in_(ids))
            assert len(session.scalars(stmt).all()) == 3
        """
    )
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(passed=1)
    result.stdout.no_fnmatch_line("*queryguard index check*")


@needs_pg
def test_pg_one_finding_per_table_and_condition(
    project: pytest.Pytester, pg16: Postgres
) -> None:
    _pg_project(project, pg16).makepyfile(
        test_a="""
        from sqlalchemy import select

        from qgmodels import Order


        def test_orders(session):
            for customer_id in (1, 2, 3):
                stmt = select(Order).where(Order.customer_id == customer_id)
                session.scalars(stmt).all()
            stmt = select(Order.id).where(Order.customer_id == 1)
            session.scalars(stmt).all()
        """
    )
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=1)
    # On CI pytest repeats the whole message in the short summary; count once.
    failures = result.stdout.str().split("short test summary info")[0]
    assert failures.count("is read in full") == 1


@needs_pg
def test_pg_budget_nplusone_and_index_findings_share_one_failure(
    project: pytest.Pytester, pg16: Postgres
) -> None:
    _pg_project(project, pg16).makepyfile(
        test_a="""
        import pytest
        from sqlalchemy import select

        from qgmodels import Customer, Order


        @pytest.mark.max_queries(1)
        def test_all(session):
            for customer in session.scalars(select(Customer)).all():
                stmt = select(Order).where(Order.customer_id == customer.id)
                session.scalars(stmt).all()
            for customer_id in (1, 2):
                stmt = select(Order.id).where(Order.customer_id == customer_id)
                session.scalars(stmt).all()
        """
    )
    project.makeini("[pytest]\nqueryguard_repeat_threshold = 3\n")
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "*queryguard: expected at most 1 queries, got 6",
            "*queryguard: N+1 detected",
            "*queryguard: missing index",
        ]
    )


@needs_pg
def test_pg_check_leaves_the_transaction_and_settings_alone(
    project: pytest.Pytester, pg16: Postgres
) -> None:
    _pg_project(project, pg16).makepyfile(
        test_a="""
        from sqlalchemy import select, text

        from conftest import SCHEMA
        from qgmodels import Customer, Order


        def test_state(session):
            session.add(Customer(id=9, name="new"))
            session.flush()
            # a table created in this still-open transaction is visible to the check
            session.execute(text("CREATE TABLE scratch (a int)"))
            session.execute(text("SELECT a FROM scratch WHERE a = :a"), {"a": 1})
            # This statement moves search_path away from the test's schema
            # while it runs, so planning it again afterwards fails.
            session.execute(
                text(
                    "SELECT set_config('search_path', 'nowhere', true), count(*) "
                    "FROM orders"
                )
            )
            session.execute(
                text("SELECT set_config('search_path', :schema, true)"),
                {"schema": SCHEMA},
            )
            assert session.scalar(text("SHOW enable_seqscan")) == "on"
            assert session.scalar(text("SHOW statement_timeout")) == "0"
            # the transaction is alive and still holds the uncommitted row
            name = text("SELECT name FROM customers WHERE id = 9")
            assert session.execute(name).scalar_one() == "new"
            assert session.connection().in_transaction()
            session.rollback()
            assert session.get(Customer, 9) is None
        """
    )
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "  scratch is read in full: Seq Scan with filter (scratch.a = $1)",
            "*= queryguard index check =*",
            (
                "1 statement could not be explained; the first: "
                'relation "orders" does not exist'
            ),
            "  SELECT set_config('search_path', 'nowhere', true), count(*) FROM orders",
        ]
    )
    # the failure is the missing index, not an error from the test's own asserts
    result.stdout.no_fnmatch_line("*AssertionError*")


@needs_pg
def test_pg_autocommit_connection(project: pytest.Pytester, pg16: Postgres) -> None:
    _pg_project(project, pg16).makepyfile(
        test_a="""
        from sqlalchemy import select, text

        from conftest import make_engine
        from qgmodels import Order


        def test_autocommit(engine):
            auto = make_engine(isolation_level="AUTOCOMMIT")
            try:
                with auto.connect() as conn:
                    stmt = select(Order.id).where(Order.customer_id == 1)
                    assert len(conn.execute(stmt).all()) == 2
                    assert conn.scalar(text("SHOW enable_seqscan")) == "on"
            finally:
                auto.dispose()
        """
    )
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["  orders is read in full: Seq Scan with filter*"])
    result.stdout.no_fnmatch_line("*AssertionError*")


@needs_pg
def test_pg_fixture_queries_are_not_checked(
    project: pytest.Pytester, pg16: Postgres
) -> None:
    _pg_project(project, pg16).makepyfile(
        test_a="""
        import pytest
        from sqlalchemy import select

        from qgmodels import Order


        @pytest.fixture
        def orders(session):
            return session.scalars(select(Order).where(Order.customer_id == 1)).all()


        def test_orders(orders):
            assert len(orders) == 2
        """
    )
    project.runpytest("--queryguard-index-check").assert_outcomes(passed=1)


@needs_pg
def test_pg_before_16_gets_one_notice(
    project: pytest.Pytester, postgres: Postgres
) -> None:
    if postgres.version >= (16,):
        pytest.skip("needs PostgreSQL older than 16")
    _pg_project(project, postgres).makepyfile(test_a=ORDERS_OF_CUSTOMER)
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(passed=1)
    shown = ".".join(str(part) for part in postgres.version)
    result.stdout.fnmatch_lines(
        [
            (
                f"PostgreSQL {shown} statements were not checked: "
                "the index check needs PostgreSQL 16 or newer"
            )
        ]
    )


ORDERS_ON_A_NEWER_SERVER = """
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from qgmodels import Order


@pytest.fixture
def session(engine):
    # what SQLAlchemy reads from a server newer than the tested ones
    engine.dialect.server_version_info = (19, 0)
    with Session(engine) as session:
        yield session


def test_orders(session):
    stmt = select(Order).where(Order.customer_id == 1)
    assert len(session.scalars(stmt).all()) == 2


def test_more_orders(session):
    stmt = select(Order).where(Order.customer_id == 2)
    assert len(session.scalars(stmt).all()) == 2
"""


@needs_pg
def test_pg_newer_than_tested_is_checked_with_one_notice(
    project: pytest.Pytester, pg16: Postgres
) -> None:
    _pg_project(project, pg16).makepyfile(test_a=ORDERS_ON_A_NEWER_SERVER)
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=2)
    notice = (
        "PostgreSQL 19.0 is newer than the versions the index check is tested "
        "on (16 to 18): its statements are checked, but a finding may be wrong "
        "or missing; please report one that is"
    )
    assert result.stdout.str().count(notice) == 1
    result.stdout.fnmatch_lines(["*orders is read in full: Seq Scan*"])


# A table with columns for the pagination and cast cases, created inside the
# test's own transaction (the check sees it), with or without statistics.
ITEMS = """
import pytest
from sqlalchemy import text

SETUP = [
    "CREATE TABLE owners (id int PRIMARY KEY, name text)",
    "CREATE TABLE items (id serial PRIMARY KEY, owner_id int, tenant_id int, "
    "status text, total numeric, created_at timestamp)",
    "CREATE INDEX items_tenant_status ON items (tenant_id, status)",
    "INSERT INTO owners SELECT g, 'o' || g FROM generate_series(1, 5) g",
    "INSERT INTO items (owner_id, tenant_id, status, total, created_at) "
    "SELECT g % 5 + 1, g % 3, 's' || (g % 4), g, now() "
    "FROM generate_series(1, 30) g",
]


@pytest.fixture(params=[False, True], ids=["no-stats", "analyzed"])
def db(session, request):
    for statement in SETUP:
        session.execute(text(statement))
    if request.param:
        session.execute(text("ANALYZE owners"))
        session.execute(text("ANALYZE items"))
    return session
"""


def _items_project(project: pytest.Pytester, pg: Postgres, body: str) -> None:
    _pg_project(project, pg)
    project.makepyfile(test_a=ITEMS + "\n\n" + textwrap.dedent(body))


@needs_pg
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM items WHERE total > 0 ORDER BY id LIMIT 20",
        "SELECT * FROM items WHERE created_at IS NULL ORDER BY id DESC LIMIT 20",
    ],
)
def test_pg_pagination_on_the_primary_key_is_not_reported(
    project: pytest.Pytester, pg16: Postgres, sql: str
) -> None:
    _items_project(
        project,
        pg16,
        f"""
        def test_page(db):
            db.execute(text("{sql}")).all()
        """,
    )
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(passed=2)


@needs_pg
def test_pg_equality_behind_order_by_limit_is_reported(
    project: pytest.Pytester, pg16: Postgres
) -> None:
    _items_project(
        project,
        pg16,
        """
        def test_page(db):
            sql = "SELECT * FROM items WHERE owner_id = :o ORDER BY id LIMIT 10"
            db.execute(text(sql), {"o": 1}).all()
        """,
    )
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=2)
    lines = [
        (
            "  items is read in full: Index Scan using items_pkey with filter "
            "(items.owner_id = $1)"
        ),
        "*",
        "*",
        "  fix: CREATE INDEX ON items (owner_id)",
    ]
    result.stdout.fnmatch_lines(lines + lines)


@needs_pg
def test_pg_trailing_column_of_a_multicolumn_index_is_reported(
    project: pytest.Pytester, pg16: Postgres
) -> None:
    if pg16.version >= (18,):
        pytest.skip("PostgreSQL 18 can skip-scan the leading column")
    _items_project(
        project,
        pg16,
        """
        def test_status(db):
            db.execute(text("SELECT * FROM items WHERE status = :s"), {"s": "s1"}).all()
        """,
    )
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=2)
    result.stdout.fnmatch_lines(
        [
            (
                "  items is read in full: *Index Scan using items_tenant_status "
                "with index condition (items.status = $1)"
            ),
            "*",
            "*",
            "  fix: CREATE INDEX ON items (status)",
        ]
    )


@needs_pg
def test_pg_leading_column_of_a_multicolumn_index_passes(
    project: pytest.Pytester, pg16: Postgres
) -> None:
    _items_project(
        project,
        pg16,
        """
        def test_tenant(db):
            sql = "SELECT * FROM items WHERE tenant_id = :t AND status = :s"
            db.execute(text(sql), {"t": 1, "s": "s1"}).all()
            db.execute(text("SELECT * FROM items WHERE tenant_id = :t"), {"t": 1}).all()
        """,
    )
    project.runpytest("--queryguard-index-check").assert_outcomes(passed=2)


@needs_pg
@pytest.mark.parametrize(
    "condition", ["total::int = :x", "created_at::date = CAST(:x AS date)"]
)
def test_pg_cast_column_gets_no_create_index_hint(
    project: pytest.Pytester, pg16: Postgres, condition: str
) -> None:
    value = '"2026-01-01"' if "date" in condition else "3"
    _items_project(
        project,
        pg16,
        f"""
        def test_cast(db):
            db.execute(text("CREATE INDEX ON items (total)"))
            db.execute(text("CREATE INDEX ON items (created_at)"))
            sql = "SELECT * FROM items WHERE {condition}"
            db.execute(text(sql), {{"x": {value}}}).all()
        """,
    )
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=2)
    result.stdout.fnmatch_lines(
        [
            "  items is read in full: Seq Scan with filter *",
            "*",
            "*",
            (
                "  fix: add an index that matches this condition "
                "(an expression index may be needed)"
            ),
        ]
    )
    result.stdout.no_fnmatch_line("*CREATE INDEX ON*")


@needs_pg
@pytest.mark.parametrize(
    "prefix", ["/* app=shop,route=/orders */ ", "-- tagged\\n", "/* a */ -- b\\n"]
)
def test_pg_statements_after_comments_are_checked(
    project: pytest.Pytester, pg16: Postgres, prefix: str
) -> None:
    _items_project(
        project,
        pg16,
        f"""
        def test_tagged(db):
            sql = "{prefix}SELECT * FROM items WHERE owner_id = :o"
            db.execute(text(sql), {{"o": 1}}).all()
        """,
    )
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=2)
    result.stdout.fnmatch_lines(["  fix: CREATE INDEX ON items (owner_id)"])


@needs_pg
def test_pg_one_finding_for_one_column_under_two_aliases(
    project: pytest.Pytester, pg16: Postgres
) -> None:
    _items_project(
        project,
        pg16,
        """
        def test_union(db):
            sql = (
                "SELECT i.id FROM items i WHERE i.owner_id = :o "
                "UNION ALL SELECT j.id FROM items j WHERE j.owner_id = :p"
            )
            db.execute(text(sql), {"o": 1, "p": 2}).all()
        """,
    )
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=2)
    failures = result.stdout.str().split("short test summary info")[0]
    assert failures.count("is read in full") == 2  # once per parametrized test


@needs_pg
@pytest.mark.parametrize(
    "sql",
    [
        # a correlated scalar subquery: the sub-plan scans with a filter
        (
            "SELECT o.id, (SELECT count(*) FROM items i WHERE i.owner_id = o.id) "
            "FROM owners o"
        ),
        # EXISTS behind a selective outer filter
        (
            "SELECT * FROM owners o WHERE o.id = :o "
            "AND EXISTS (SELECT 1 FROM items i WHERE i.owner_id = o.id)"
        ),
    ],
)
def test_pg_subqueries_are_reported_through_their_filter(
    project: pytest.Pytester, pg16: Postgres, sql: str
) -> None:
    _items_project(
        project,
        pg16,
        f"""
        def test_subquery(db):
            db.execute(text("{sql}"), {{"o": 1}}).all()
        """,
    )
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=2)
    result.stdout.fnmatch_lines(
        [
            "  items is read in full: Seq Scan with filter (i.owner_id = *)",
            "*",
            "*",
            "  fix: CREATE INDEX ON items (owner_id)",
        ]
    )


@needs_pg
def test_pg_join_without_a_filter_is_not_reported(
    project: pytest.Pytester, pg16: Postgres
) -> None:
    _items_project(
        project,
        pg16,
        """
        def test_join(db):
            sql = "SELECT o.name, i.id FROM owners o JOIN items i ON i.owner_id = o.id"
            assert len(db.execute(text(sql)).all()) == 30
        """,
    )
    project.runpytest("--queryguard-index-check").assert_outcomes(passed=2)


@needs_pg
@pytest.mark.parametrize(
    ("column", "hint"),
    [
        ("label", "  fix: CREATE INDEX ON casts (label)"),
        (
            "id",
            (
                "  fix: add an index that matches this condition "
                "(an expression index may be needed)"
            ),
        ),
        (
            "kind",
            (
                "  fix: add an index that matches this condition "
                "(an expression index may be needed)"
            ),
        ),
        (
            "code",
            (
                "  fix: add an index that matches this condition "
                "(an expression index may be needed)"
            ),
        ),
    ],
)
def test_pg_text_cast_hint_only_for_varchar_columns(
    project: pytest.Pytester, pg16: Postgres, column: str, hint: str
) -> None:
    # PostgreSQL prints (col)::text for every cast to text; only for varchar
    # does an index on the plain column serve it.
    _pg_project(project, pg16).makepyfile(
        test_a=f"""
        from sqlalchemy import text


        def test_cast(session):
            for sql in [
                "CREATE TYPE cast_kind AS ENUM ('a', 'b')",
                "CREATE TABLE casts (id int PRIMARY KEY, label varchar(20), "
                "kind cast_kind, code char(4))",
                "INSERT INTO casts SELECT g, 'l' || g, 'a', 'c' FROM "
                "generate_series(1, 20) g",
            ]:
                session.execute(text(sql))
            sql = "SELECT * FROM casts WHERE {column}::text = :s"
            session.execute(text(sql), {{"s": "x"}}).all()
        """
    )
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["  casts is read in full: *", "*", "*", hint])


@needs_pg
def test_pg_temporary_table_reports_the_trailing_column(
    project: pytest.Pytester, pg16: Postgres
) -> None:
    # EXPLAIN VERBOSE says "pg_temp"; pg_namespace has "pg_temp_<n>".
    if pg16.version >= (18,):
        pytest.skip("PostgreSQL 18 can skip-scan the leading column")
    _pg_project(project, pg16).makepyfile(
        test_a="""
        from sqlalchemy import text


        def test_temp(session):
            for sql in [
                "CREATE TEMPORARY TABLE tmp_items (id int PRIMARY KEY, "
                "tenant_id int, status text)",
                "CREATE INDEX ON tmp_items (tenant_id, status)",
                "INSERT INTO tmp_items SELECT g, g % 3, 's' || g % 4 "
                "FROM generate_series(1, 30) g",
            ]:
                session.execute(text(sql))
            sql = "SELECT * FROM tmp_items WHERE status = :s"
            session.execute(text(sql), {"s": "s1"}).all()
        """
    )
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "  pg_temp.tmp_items is read in full: *Index Scan using *",
            "*",
            "*",
            "  fix: CREATE INDEX ON pg_temp.tmp_items (status)",
        ]
    )


@needs_pg
def test_pg_temporary_table_with_a_varchar_column_gets_the_index_hint(
    project: pytest.Pytester, pg16: Postgres
) -> None:
    _pg_project(project, pg16).makepyfile(
        test_a="""
        from sqlalchemy import text


        def test_temp(session):
            for sql in [
                "CREATE TEMPORARY TABLE tmp_casts (id int PRIMARY KEY, "
                "label varchar(20))",
                "INSERT INTO tmp_casts SELECT g, 'l' || g FROM "
                "generate_series(1, 20) g",
            ]:
                session.execute(text(sql))
            sql = "SELECT * FROM tmp_casts WHERE label::text = :s"
            session.execute(text(sql), {"s": "x"}).all()
        """
    )
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "  pg_temp.tmp_casts is read in full: *",
            "*",
            "*",
            "  fix: CREATE INDEX ON pg_temp.tmp_casts (label)",
        ]
    )


@needs_pg
@pytest.mark.parametrize("method", ["brin", "gin"])
def test_pg_trailing_column_of_brin_and_gin_indexes_passes(
    project: pytest.Pytester, pg16: Postgres, method: str
) -> None:
    # Only btree reads the whole index for a condition on a trailing column.
    _pg_project(project, pg16).makepyfile(
        test_a=f"""
        from sqlalchemy import text


        def test_events(session):
            for sql in [
                "CREATE EXTENSION IF NOT EXISTS btree_gin",
                "CREATE TABLE events (id int PRIMARY KEY, tenant_id int, kind int)",
                "INSERT INTO events SELECT g, g % 3, g % 4 FROM generate_series(1, 40) g",
                "CREATE INDEX ON events USING {method} (tenant_id, kind)",
            ]:
                session.execute(text(sql))
            sql = "SELECT id FROM events WHERE kind = :k"
            session.execute(text(sql), {{"k": 1}}).all()
        """
    )
    project.runpytest("--queryguard-index-check").assert_outcomes(passed=1)


@needs_pg
def test_pg_whole_include_index_is_reported_with_and_without_statistics(
    project: pytest.Pytester, pg16: Postgres
) -> None:
    # A committed table: before ANALYZE PostgreSQL reads the INCLUDE index
    # as a bitmap, after it as an index-only scan. Both read it in full.
    _pg_project(project, pg16).makepyfile(
        test_a="""
        import pytest
        from sqlalchemy import text


        @pytest.fixture(params=[False, True], ids=["no-stats", "analyzed"])
        def notes(engine, request):
            with engine.begin() as conn:
                conn.execute(text(
                    "CREATE TABLE notes (id serial PRIMARY KEY, "
                    "created_at timestamp, note text)"
                ))
                conn.execute(text(
                    "CREATE INDEX notes_inc ON notes (created_at) INCLUDE (note)"
                ))
                conn.execute(text(
                    "INSERT INTO notes (created_at, note) "
                    "SELECT now(), 'n' || g FROM generate_series(1, 6) g"
                ))
            if request.param:
                with engine.begin() as conn:
                    conn.execute(text("ANALYZE notes"))
            yield request.param
            with engine.begin() as conn:
                conn.execute(text("SET LOCAL lock_timeout = '5s'"))
                conn.execute(text("DROP TABLE notes"))


        # notes before session, so the session ends before the table is dropped
        def test_note(notes, session):
            sql = "SELECT note FROM notes WHERE note = :n"
            session.execute(text(sql), {"n": "n1"}).all()
        """
    )
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=2)
    result.stdout.fnmatch_lines(
        [
            "*test_note?no-stats?*",
            (
                "  notes is read in full: Bitmap Heap Scan using notes_inc with filter "
                "(notes.note = $1)"
            ),
            "*test_note?analyzed?*",
            (
                "  notes is read in full: Index Only Scan using notes_inc with filter "
                "(notes.note = $1)"
            ),
        ]
    )


# A partial index serves only the rows of its predicate. Before ANALYZE, the
# planner reads it in full as a bitmap, after it as an index scan; in both
# cases nothing narrows the read by email (issue #4).
USERS = """
import pytest
from sqlalchemy import text

SETUP = [
    "CREATE TABLE users (id serial PRIMARY KEY, email text, status text, "
    "a int, b int, created_at timestamp, deleted_at timestamp)",
    "CREATE INDEX users_live ON users (id) WHERE deleted_at IS NULL",
    "CREATE INDEX users_status ON users (status)",
    "CREATE INDEX users_a ON users (a)",
    "CREATE INDEX users_b ON users (b)",
    "INSERT INTO users (email, status, a, b, created_at, deleted_at) "
    "SELECT 'e' || g, 's' || (g % 4), g % 7, g % 11, now(), "
    "CASE WHEN g % 5 = 0 THEN now() END FROM generate_series(1, 30) g",
]


@pytest.fixture(params=[False, True], ids=["no-stats", "analyzed"])
def db(session, request):
    for statement in SETUP:
        session.execute(text(statement))
    if request.param:
        session.execute(text("ANALYZE users"))
    return session
"""


def _users_project(project: pytest.Pytester, pg: Postgres, body: str) -> None:
    _pg_project(project, pg)
    project.makepyfile(test_a=USERS + "\n\n" + textwrap.dedent(body))


@needs_pg
def test_pg_whole_partial_index_is_reported_with_and_without_statistics(
    project: pytest.Pytester, pg16: Postgres
) -> None:
    _users_project(
        project,
        pg16,
        """
        def test_live_user(db):
            sql = "SELECT id FROM users WHERE deleted_at IS NULL AND email = :e"
            db.execute(text(sql), {"e": "e1"}).all()
        """,
    )
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=2)
    result.stdout.fnmatch_lines(
        [
            "*test_live_user?no-stats?*",
            (
                "  users is read in full: Bitmap Heap Scan using users_live "
                "with filter (users.email = $1)"
            ),
            "*",
            "*",
            "  fix: CREATE INDEX ON users (email)",
            "*test_live_user?analyzed?*",
            (
                "  users is read in full: Index Scan using users_live "
                "with filter (users.email = $1)"
            ),
            "*",
            "*",
            "  fix: CREATE INDEX ON users (email)",
        ]
    )


@needs_pg
@pytest.mark.parametrize(
    ("sql", "params"),
    [
        # BitmapOr of two indexed conditions
        ("SELECT id FROM users WHERE a = :a OR b = :b", "a=1, b=2"),
        # BitmapAnd before ANALYZE, one index condition after it
        ("SELECT id FROM users WHERE a = :a AND b = :b", "a=1, b=2"),
        # a bitmap with a real index condition, filtered further
        ("SELECT id FROM users WHERE status = :s AND email = :e", "s='s1', e='e1'"),
        # the partial index plus an indexed equality
        (
            (
                "SELECT id FROM users WHERE deleted_at IS NULL AND status = :s "
                "AND email = :e"
            ),
            "s='s1', e='e1'",
        ),
        # a whole partial index with a range filter: pagination-like
        (
            "SELECT id FROM users WHERE deleted_at IS NULL AND created_at > :t",
            "t=datetime.datetime(2026, 1, 1)",
        ),
        # one arm of a BitmapOr reads the whole partial index
        (
            (
                "SELECT id FROM users WHERE (deleted_at IS NULL AND email = :e) "
                "OR a = :a"
            ),
            "e='e1', a=1",
        ),
    ],
)
def test_pg_other_bitmap_scans_are_not_reported(
    project: pytest.Pytester, pg16: Postgres, sql: str, params: str
) -> None:
    _users_project(
        project,
        pg16,
        f"""
        import datetime


        def test_bitmap(db):
            db.execute(text("{sql}"), dict({params})).all()
        """,
    )
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(passed=2)


# The catalog lookups are cached for the session, per engine (issue #5). A
# session-scoped engine keeps its cache from test to test, so DDL in a
# fixture, and a rollback of DDL inside a test, must clear it.
SESSION_ENGINE_CONFTEST = """
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

URL = {url!r}
SCHEMA = {schema!r}


@pytest.fixture(scope="session")
def engine():
    engine = create_engine(
        URL, connect_args={{"startup_params": {{"search_path": SCHEMA}}}}
    )
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE events (id int PRIMARY KEY, a int, b int)"))
        conn.execute(text("CREATE INDEX events_ab ON events (a, b)"))
        conn.execute(text(
            "INSERT INTO events SELECT g, g % 3, g % 4 FROM generate_series(1, 30) g"
        ))
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture
def redefined(engine):
    with engine.begin() as conn:
        conn.execute(text("DROP INDEX events_ab"))
        conn.execute(text("CREATE INDEX events_ab ON events (b, a)"))
"""


@needs_pg
def test_pg_catalog_cache_is_cleared_by_ddl_and_by_its_rollback(
    project: pytest.Pytester, pg16: Postgres
) -> None:
    if pg16.version >= (18,):
        pytest.skip("PostgreSQL 18 can skip-scan the leading column")
    project.makeconftest(
        SESSION_ENGINE_CONFTEST.format(url=pg16.url, schema=pg16.schema)
    )
    project.makepyfile(
        test_a="""
        from sqlalchemy import text


        def test_leading_column(session):
            session.execute(text("SELECT id FROM events WHERE a = :v"), {"v": 1}).all()


        def test_redefined_in_a_fixture(redefined, session):
            # events_ab is (b, a) now: a is its trailing column
            session.execute(text("SELECT id FROM events WHERE a = :v"), {"v": 1}).all()


        def test_redefinition_rolled_back(session):
            session.execute(text("DROP INDEX events_ab"))
            session.execute(text("CREATE INDEX events_ab ON events (a, b)"))
            session.execute(text("SELECT id FROM events WHERE a = :v"), {"v": 1}).all()
            session.rollback()
            # events_ab is (b, a) again
            sql = "SELECT id, b FROM events WHERE a = :v"
            session.execute(text(sql), {"v": 1}).all()  # QG_LINE


        def test_same_statement_after_rollback(session):
            sql = "SELECT id, a FROM events WHERE a = :v"
            session.execute(text("DROP INDEX events_ab"))
            session.execute(text("CREATE INDEX events_ab ON events (a, b)"))
            session.execute(text(sql), {"v": 1}).all()
            session.rollback()
            # events_ab is (b, a) again and the very same statement runs again
            session.execute(text(sql), {"v": 1}).all()
        """
    )
    path = project.path / "test_a.py"
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(passed=1, failed=3)
    result.stdout.fnmatch_lines(
        [
            "*_ test_redefined_in_a_fixture _*",
            (
                "  events is read in full: *Index * using events_ab "
                "with index condition (events.a = $1)"
            ),
            "*_ test_redefinition_rolled_back _*",
            (
                "  events is read in full: *Index * using events_ab "
                "with index condition (events.a = $1)"
            ),
            f"  first at test_a.py:{_line(path)} in test_redefinition_rolled_back",
            "*_ test_same_statement_after_rollback _*",
            (
                "  events is read in full: *Index * using events_ab "
                "with index condition (events.a = $1)"
            ),
        ]
    )


def test_each_session_starts_with_an_empty_catalog_cache(
    project: pytest.Pytester,
) -> None:
    from pytest_queryguard import catalog

    class Engine:
        pass

    engine = Engine()
    catalog.for_engine(engine).leading[("public", "i")] = "a"
    project.makepyfile(test_a=SQLITE_TEST)
    project.runpytest("--queryguard-index-check").assert_outcomes(passed=1)
    assert catalog.for_engine(engine).leading == {}


# A partial index may serve a statement that the generic plan cannot prove it
# serves: the bound values are parameters there. The check then reports
# nothing for the statement and counts it in the session summary.
PARTIAL = """
import pytest
from sqlalchemy import column, select, table, text

SETUP = [
    "CREATE TABLE t1 (status varchar, x int, flag boolean)",
    "CREATE INDEX t1_partial ON t1 (x) WHERE status = 'open'",
    "CREATE INDEX t1_flagged ON t1 (x) WHERE flag",
    "INSERT INTO t1 SELECT CASE WHEN g % 50 = 0 THEN 'open' ELSE 'done' END, "
    "g, g % 2 = 0 FROM generate_series(1, 5000) g",
    "ANALYZE t1",
]
t1 = table("t1", column("status"), column("x"), column("flag"))


@pytest.fixture
def db(session):
    for statement in SETUP:
        session.execute(text(statement))
    return session
"""


def _partial_project(project: pytest.Pytester, pg: Postgres, body: str) -> None:
    _pg_project(project, pg)
    project.makepyfile(test_a=PARTIAL + "\n\n" + textwrap.dedent(body))


@needs_pg
@pytest.mark.parametrize("advisor", [False, True], ids=["plain", "advisor"])
def test_pg_a_partial_index_that_may_serve_the_statement_is_not_reported(
    project: pytest.Pytester, pg16: Postgres, advisor: bool
) -> None:
    _partial_project(
        project,
        pg16,
        """
        def test_open(db):
            stmt = select(t1).where(t1.c.status == "open", t1.c.x == 10)
            db.execute(stmt).all()
        """,
    )
    result = project.runpytest(
        "--queryguard-index-check", "-o", f"queryguard_index_advisor={advisor}"
    )
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(
        [
            "*queryguard index check*",
            (
                "1 statement skipped: a partial index on t1 may serve it; "
                "the generic plan cannot tell"
            ),
        ]
    )
    result.stdout.no_fnmatch_line("*missing index*")
    result.stdout.no_fnmatch_line("*CREATE INDEX*")


@needs_pg
def test_pg_the_same_statement_text_is_counted_once(
    project: pytest.Pytester, pg16: Postgres
) -> None:
    _partial_project(
        project,
        pg16,
        """
        def test_open(db):
            db.execute(select(t1).where(t1.c.status == "open", t1.c.x == 10)).all()
            db.execute(select(t1).where(t1.c.status == "open", t1.c.x == 20)).all()
            db.execute(select(t1).where(t1.c.status == "done", t1.c.x > 20)).all()
        """,
    )
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(
        ["2 statements skipped: a partial index on t1 may serve them; *"]
    )


@needs_pg
def test_pg_a_partial_index_with_another_predicate_still_reports(
    project: pytest.Pytester, pg16: Postgres
) -> None:
    # t1_flagged is on (x) WHERE flag; the statement never mentions flag, so
    # it cannot use it and nothing may serve it: the finding stands.
    _partial_project(
        project,
        pg16,
        """
        def test_name(db):
            db.execute(text("DROP INDEX t1_partial"))
            stmt = select(t1).where(t1.c.status == "open", t1.c.x == 10)
            db.execute(stmt).all()
        """,
    )
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*queryguard: missing index*", "*t1 is read in full*"])
    result.stdout.no_fnmatch_line("*statement skipped*")


@needs_pg
def test_pg_dropping_the_partial_index_makes_the_statement_reportable_again(
    project: pytest.Pytester, pg16: Postgres
) -> None:
    _partial_project(
        project,
        pg16,
        """
        def test_open(db):
            stmt = select(t1).where(t1.c.status == "open", t1.c.x == 10)
            db.execute(stmt).all()
            db.execute(text("DROP INDEX t1_partial"))
            db.execute(stmt).all()
        """,
    )
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*queryguard: missing index*", "*t1 is read in full*"])


BROKEN_FIND_SCANS = """


@pytest.fixture(autouse=True)
def broken_find_scans(monkeypatch):
    from pytest_queryguard import indexes

    def broken(*args):
        raise RuntimeError("find_scans broke")

    monkeypatch.setattr(indexes, "find_scans", broken)
"""


SAVEPOINT_SCENARIOS = {
    "autocommit": """
        from sqlalchemy import text


        def test_autocommit_write_is_committed(engine):
            auto = engine.execution_options(isolation_level="AUTOCOMMIT")
            with auto.connect() as conn:
                conn.execute(text("SELECT * FROM orders WHERE customer_id = 1")).all()
                conn.execute(text("INSERT INTO orders (id, customer_id) VALUES (99, 1)"))
            with engine.connect() as other:
                count = other.execute(text("SELECT count(*) FROM orders WHERE id = 99"))
                assert count.scalar() == 1
        """,
    "transaction": """
        from sqlalchemy import text


        def test_transaction_settings_are_unchanged(engine):
            with engine.connect() as conn:
                conn.execute(text("SELECT * FROM orders WHERE customer_id = 1")).all()
                assert conn.execute(text("SHOW enable_seqscan")).scalar() == "on"
                conn.rollback()
        """,
}


@needs_pg
@pytest.mark.parametrize("scenario", sorted(SAVEPOINT_SCENARIOS))
def test_pg_a_bug_inside_the_savepoint_leaves_the_connection_as_it_was(
    project: pytest.Pytester, pg16: Postgres, scenario: str
) -> None:
    # The bug stops the plugin; the user's connection must not keep the
    # plugin's transaction (autocommit) or its savepoint and enable_seqscan =
    # off (inside the user's transaction).
    _pg_project(project, pg16)
    conftest = project.path / "conftest.py"
    conftest.write_text(conftest.read_text() + BROKEN_FIND_SCANS)
    project.makepyfile(test_a=SAVEPOINT_SCENARIOS[scenario])
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(passed=1, warnings=1)
    result.stdout.fnmatch_lines(["*QueryGuardInternalWarning*find_scans broke*"])
