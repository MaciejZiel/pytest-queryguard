"""The missing-index check, end to end.

Tests that need PostgreSQL run only when QUERYGUARD_PG_URL is set, for
example ``postgresql+pg8000://postgres:pg@localhost:5432/postgres``.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

# pytester runs sessions in-process, so the outer plugin sees their queries too.
pytestmark = pytest.mark.allow_nplusone

MODELS = (Path(__file__).parent / "models.py").read_text()

SQLITE_TEST = """
from qgmodels import Order, make_sync_session


def test_sqlite():
    session = make_sync_session()
    assert session.query(Order).filter(Order.customer_id == 1).count() == 2
"""

SQLITE_NOTICE = (
    "sqlite statements were not checked: the index check needs PostgreSQL 16 or newer"
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
def test_pg_join_condition_is_reported(
    project: pytest.Pytester, pg16: Postgres
) -> None:
    _pg_project(project, pg16).makepyfile(
        test_a="""
        from sqlalchemy import select

        from qgmodels import Customer, Order


        def test_join(session):
            stmt = select(Customer.name, Order.id).join(Order.customer)
            assert len(session.execute(stmt).all()) == 6
        """
    )
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "  orders is read in full: Seq Scan for join condition *customer_id*",
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
