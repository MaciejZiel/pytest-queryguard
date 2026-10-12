"""The index advisor and its hypopg verification against a real PostgreSQL.

Needs QUERYGUARD_PG_URL like tests/test_index_check.py, and the hypopg
extension on the server (the Debian package postgresql-<major>-hypopg); the
tests are skipped, not failed, where it is not available.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

from pytest_queryguard import hypopg, indexes
from pytest_queryguard.events import Catalog, ScanIssue
from pytest_queryguard.indexes import AdvisorOptions, Explained
from tests import test_index_check as sync_tests

PG_URL = sync_tests.PG_URL
Postgres = sync_tests.Postgres
needs_pg = sync_tests.needs_pg
project = sync_tests.project
postgres = sync_tests.postgres
pg16 = sync_tests.pg16
_pg_project = sync_tests._pg_project
ORDERS_OF_CUSTOMER = sync_tests.ORDERS_OF_CUSTOMER

# pytester runs sessions in-process, so the outer plugin sees their queries too.
pytestmark = [pytest.mark.allow_nplusone, needs_pg]


@pytest.fixture
def with_hypopg(pg16: Postgres) -> Postgres:
    """The test database with the extension created, or a skip."""
    assert PG_URL is not None
    engine = create_engine(PG_URL, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            available = conn.execute(
                text("SELECT 1 FROM pg_available_extensions WHERE name = 'hypopg'")
            ).first()
            if available is None:
                pytest.skip("the hypopg extension is not available on this server")
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS hypopg"))
    finally:
        engine.dispose()
    return pg16


@dataclass
class Db:
    engine: Engine
    schema: str

    def run(self, *statements: str) -> None:
        with self.engine.begin() as conn:
            for statement in statements:
                conn.exec_driver_sql(statement)


@pytest.fixture
def db(with_hypopg: Postgres) -> Iterator[Db]:
    """Tables in the test's own schema (the search_path), 20000 rows each."""
    engine = create_engine(
        with_hypopg.url,
        connect_args={"startup_params": {"search_path": with_hypopg.schema}},
    )
    database = Db(engine, with_hypopg.schema)
    database.run(
        "CREATE TABLE orders (id serial PRIMARY KEY, customer_id int, "
        "status varchar(20), total numeric, created_at timestamp, "
        "deleted_at timestamp, note text)",
        "INSERT INTO orders SELECT g, g % 200, 'x', g, now() - g * interval '1 s', "
        "CASE WHEN g % 50 = 0 THEN NULL ELSE now() END, 'n' "
        "FROM generate_series(1, 20000) g",
        "CREATE TABLE customers (id serial PRIMARY KEY, email varchar(50))",
        "INSERT INTO customers SELECT g, 'c' || g FROM generate_series(1, 200) g",
        "ANALYZE",
    )
    yield database
    engine.dispose()


def advise(
    db: Db,
    sql: str,
    mode: str = "auto",
    *,
    autocommit: bool = False,
    check_clean: bool = True,
) -> Explained:
    """run_explain with the advisor on a connection of ``db``."""
    options = {"isolation_level": "AUTOCOMMIT"} if autocommit else {}
    with db.engine.connect().execution_options(**options) as conn:
        outcome = indexes.run_explain(
            conn.connection.dbapi_connection,
            sql,
            leading=True,
            catalog=Catalog(),
            advise=AdvisorOptions(mode, 17),
        )
        assert isinstance(outcome, Explained), outcome
        if check_clean:
            assert_nothing_left(conn)
        return outcome


def assert_nothing_left(conn: Any) -> None:
    """No hypothetical index and no changed setting on the connection."""
    schema = conn.exec_driver_sql(
        "SELECT extnamespace::regnamespace::text FROM pg_extension "
        "WHERE extname = 'hypopg'"
    ).scalar()
    count = conn.exec_driver_sql(f"SELECT count(*) FROM {schema}.hypopg()").scalar()
    assert count == 0
    assert conn.exec_driver_sql("SHOW enable_seqscan").scalar() == "on"
    assert conn.exec_driver_sql("SELECT 1").scalar() == 1


def only(outcome: Explained) -> ScanIssue:
    assert outcome.issues is not None
    [issue] = outcome.issues
    return issue


def test_a_missing_index_gets_a_verified_suggestion(db: Db) -> None:
    issue = only(advise(db, "SELECT * FROM orders WHERE customer_id = $1"))
    advice = issue.advice
    assert advice is not None and advice.verification is not None
    assert advice.create_sql == "CREATE INDEX ON orders (customer_id)"
    assert advice.alembic == (
        'op.create_index("ix_orders_customer_id", "orders", ["customer_id"])'
    )
    v = advice.verification
    assert v.used is True and v.error is None
    assert v.scan in ("Index Scan", "Bitmap Index Scan")
    assert v.cost_before is not None and v.cost_after is not None
    assert v.cost_after < v.cost_before
    assert v.size is not None and v.size > 0


def test_equality_range_and_order_make_one_composite_index(db: Db) -> None:
    sql = (
        "SELECT * FROM orders WHERE customer_id = $1 AND total > $2 "
        "ORDER BY created_at DESC LIMIT 10"
    )
    advice = only(advise(db, sql)).advice
    assert advice is not None and advice.verification is not None
    assert advice.create_sql == "CREATE INDEX ON orders (customer_id, created_at DESC)"
    assert advice.verification.used is True
    assert advice.verification.scan == "Index Scan"


def test_the_range_goes_into_the_index_without_a_sort(db: Db) -> None:
    sql = "SELECT * FROM orders WHERE customer_id = $1 AND total > $2"
    advice = only(advise(db, sql)).advice
    assert advice is not None and advice.verification is not None
    assert advice.create_sql == "CREATE INDEX ON orders (customer_id, total)"
    assert advice.verification.used is True


def test_a_selective_constant_predicate_makes_a_partial_index(db: Db) -> None:
    # deleted_at IS NULL holds for 2% of the rows
    sql = "SELECT * FROM orders WHERE customer_id = $1 AND deleted_at IS NULL"
    advice = only(advise(db, sql)).advice
    assert advice is not None and advice.verification is not None
    assert advice.create_sql == (
        "CREATE INDEX ON orders (customer_id) WHERE deleted_at IS NULL"
    )
    assert advice.where == "deleted_at IS NULL"
    assert advice.alembic is not None
    assert 'postgresql_where=sa.text("deleted_at IS NULL")' in advice.alembic
    assert advice.verification.used is True


def test_an_unselective_constant_predicate_stays_a_key_column(db: Db) -> None:
    # the predicate holds for 98% of the rows: a partial index saves nothing
    sql = "SELECT * FROM orders WHERE customer_id = $1 AND deleted_at IS NOT NULL"
    outcome = advise(db, sql)
    advice = only(outcome).advice
    assert advice is not None
    assert advice.where is None
    assert advice.create_sql == "CREATE INDEX ON orders (customer_id)"


def test_two_tables_of_one_statement_are_both_advised_and_verified(db: Db) -> None:
    sql = (
        "SELECT * FROM customers c, orders o WHERE c.email = $1 AND o.customer_id = $2"
    )
    outcome = advise(db, sql)
    assert outcome.issues is not None
    suggestions = {
        i.table: i.advice.create_sql for i in outcome.issues if i.advice is not None
    }
    assert suggestions["orders"] == "CREATE INDEX ON orders (customer_id)"
    assert suggestions["customers"] == "CREATE INDEX ON customers (email)"
    for issue in outcome.issues:
        assert issue.advice is not None and issue.advice.verification is not None
        assert issue.advice.verification.used is True


def test_the_connection_is_left_as_it_was_in_autocommit_too(db: Db) -> None:
    outcome = advise(db, "SELECT * FROM orders WHERE customer_id = $1", autocommit=True)
    advice = only(outcome).advice
    assert advice is not None and advice.verification is not None
    assert advice.verification.used is True


def test_the_connection_stays_usable_inside_a_transaction(db: Db) -> None:
    with db.engine.connect() as conn:
        conn.exec_driver_sql("INSERT INTO customers VALUES (9999, 'kept')")
        outcome = indexes.run_explain(
            conn.connection.dbapi_connection,
            "SELECT * FROM orders WHERE customer_id = $1",
            advise=AdvisorOptions("auto", 17),
        )
        assert isinstance(outcome, Explained)
        assert_nothing_left(conn)
        # the test's own transaction is untouched
        count = conn.exec_driver_sql(
            "SELECT count(*) FROM customers WHERE id = 9999"
        ).scalar()
        assert count == 1
        conn.rollback()


def test_a_table_in_another_schema_is_named_with_its_schema(
    db: Db, with_hypopg: Postgres
) -> None:
    other = f"{with_hypopg.schema}_b"
    db.run(
        f"CREATE SCHEMA {other}",
        f"CREATE TABLE {other}.items (id int, owner int)",
        f"INSERT INTO {other}.items SELECT g, g % 10 FROM generate_series(1, 5000) g",
        f"ANALYZE {other}.items",
    )
    try:
        issue = only(advise(db, f"SELECT * FROM {other}.items WHERE owner = $1"))
        advice = issue.advice
        assert advice is not None and advice.verification is not None
        assert advice.create_sql == f"CREATE INDEX ON {other}.items (owner)"
        assert advice.alembic is not None and f'schema="{other}"' in advice.alembic
        assert advice.verification.used is True
    finally:
        db.run(f"DROP SCHEMA {other} CASCADE")


def test_a_temporary_table_is_advised(db: Db) -> None:
    with db.engine.connect() as conn:
        conn.exec_driver_sql("CREATE TEMP TABLE tmp_items (id int, owner int)")
        conn.exec_driver_sql(
            "INSERT INTO tmp_items SELECT g, g % 10 FROM generate_series(1, 5000) g"
        )
        conn.exec_driver_sql("ANALYZE tmp_items")
        outcome = indexes.run_explain(
            conn.connection.dbapi_connection,
            "SELECT * FROM tmp_items WHERE owner = $1",
            advise=AdvisorOptions("auto", 17),
        )
        assert isinstance(outcome, Explained)
        advice = only(outcome).advice
        assert advice is not None
        assert advice.create_sql == "CREATE INDEX ON pg_temp.tmp_items (owner)"
        assert_nothing_left(conn)
        conn.rollback()


# ------------------------------------------------------------ existing indexes


def test_an_invalid_index_is_why_no_new_one_is_suggested(db: Db) -> None:
    db.run(
        "CREATE INDEX orders_customer ON orders (customer_id)",
        "UPDATE pg_index SET indisvalid = false "
        "WHERE indexrelid = 'orders_customer'::regclass",
    )
    advice = only(advise(db, "SELECT * FROM orders WHERE customer_id = $1")).advice
    assert advice is not None
    assert advice.create_sql is None and advice.alembic is None
    assert advice.skipped is not None
    assert "orders_customer on (customer_id) exists but is marked invalid" in (
        advice.skipped
    )
    assert advice.verification is None


def test_a_partial_index_is_why_no_new_one_is_suggested(db: Db) -> None:
    db.run("CREATE INDEX orders_early ON orders (customer_id) WHERE id < 100")
    advice = only(advise(db, "SELECT * FROM orders WHERE customer_id = $1")).advice
    assert advice is not None and advice.create_sql is None
    assert advice.skipped is not None
    assert "orders_early on (customer_id) is partial (WHERE (id < 100))" in (
        advice.skipped
    )


def test_an_index_with_another_collation_is_unusable_for_a_reason_a_new_one_is_not(
    db: Db,
) -> None:
    with db.engine.connect() as conn:
        default = conn.exec_driver_sql(
            "SELECT datcollate FROM pg_database WHERE datname = current_database()"
        ).scalar()
    if default == "C":
        pytest.skip("the database collation is already C")
    db.run('CREATE INDEX orders_note_c ON orders (note COLLATE "C")')
    outcome = advise(db, "SELECT * FROM orders WHERE note = $1")
    advice = only(outcome).advice
    assert advice is not None and advice.create_sql is None
    assert advice.skipped is not None
    assert "orders_note_c on (note) exists and covers these columns" in advice.skipped


def test_a_trailing_column_of_a_composite_index_still_gets_a_suggestion(
    db: Db, with_hypopg: Postgres
) -> None:
    if with_hypopg.version >= (18,):
        pytest.skip("PostgreSQL 18 skip-scans the leading column")
    db.run("CREATE INDEX orders_cust_total ON orders (customer_id, total)")
    outcome = advise(db, "SELECT * FROM orders WHERE total = $1")
    advice = only(outcome).advice
    assert advice is not None and advice.verification is not None
    assert advice.create_sql == "CREATE INDEX ON orders (total)"
    assert advice.verification.used is True


# ----------------------------------------------------------- hypopg and errors


def test_a_failing_definition_does_not_stop_the_next_one(db: Db) -> None:
    with db.engine.connect() as conn:
        cursor = conn.connection.dbapi_connection.cursor()
        cursor.execute("SELECT 1")

        def plan_with(seqscan: bool) -> Any:
            cursor.execute(f"SELECT set_config('enable_seqscan', '{seqscan}', true)")
            cursor.execute(
                "EXPLAIN (GENERIC_PLAN, VERBOSE, FORMAT JSON) "
                "SELECT * FROM orders WHERE customer_id = $1"
            )
            return indexes._read_plan(cursor.fetchone()[0])

        results = hypopg.verify(
            cursor,
            plan_with,
            [
                "CREATE INDEX ON no_such_table (x)",
                "CREATE INDEX ON orders (customer_id)",
                "CREATE INDEX ON orders (no_such_column)",
            ],
            asyncpg=False,
        )
        assert results[0].error is not None and results[0].used is None
        assert results[1].used is True and results[1].error is None
        assert results[2].error is not None
        cursor.close()
        assert_nothing_left(conn)
        conn.rollback()


def test_a_definition_that_cannot_be_quoted_is_reported(db: Db) -> None:
    with db.engine.connect() as conn:
        cursor = conn.connection.dbapi_connection.cursor()
        results = hypopg.verify(
            cursor,
            lambda seqscan: {"Total Cost": 1.0},
            ["CREATE INDEX ON orders (customer_id) WHERE note = E'\\\\'"],
            asyncpg=False,
        )
        assert results[0].error == "the definition cannot be quoted"
        cursor.close()
        assert_nothing_left(conn)
        conn.rollback()


def test_an_unplannable_statement_marks_every_result(db: Db) -> None:
    with db.engine.connect() as conn:
        cursor = conn.connection.dbapi_connection.cursor()

        def broken(seqscan: bool) -> Any:
            cursor.execute("SELECT * FROM no_such_table")

        results = hypopg.verify(
            cursor, broken, ["CREATE INDEX ON orders (id)"] * 2, asyncpg=False
        )
        assert [r.used for r in results] == [None, None]
        assert all(r.error and "no_such_table" in r.error for r in results)
        cursor.close()
        assert_nothing_left(conn)
        conn.rollback()


def test_the_advisor_off_changes_nothing(db: Db) -> None:
    with db.engine.connect() as conn:
        outcome = indexes.run_explain(
            conn.connection.dbapi_connection,
            "SELECT * FROM orders WHERE customer_id = $1",
        )
        assert isinstance(outcome, Explained)
        assert outcome.issues is None and outcome.advisor_notice is None


def test_hypopg_off_suggests_without_verifying_and_without_a_notice(db: Db) -> None:
    outcome = advise(db, "SELECT * FROM orders WHERE customer_id = $1", "off")
    advice = only(outcome).advice
    assert advice is not None and advice.verification is None
    assert advice.create_sql == "CREATE INDEX ON orders (customer_id)"
    assert outcome.advisor_notice is None


def test_a_failing_index_lookup_leaves_the_findings_unadvised(
    db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        indexes, "_TABLE_INDEXES_LOOKUP", "SELECT * FROM no_such_table WHERE {names}"
    )
    outcome = advise(db, "SELECT * FROM orders WHERE customer_id = $1")
    assert only(outcome).advice is None
    assert outcome.advisor_notice is not None
    assert outcome.advisor_notice.startswith("index advisor: no suggestions this time")
    assert "no_such_table" in outcome.advisor_notice


@pytest.fixture
def bare_url(with_hypopg: Postgres) -> Iterator[str]:
    """A new empty database, where hypopg is available but not created."""
    assert PG_URL is not None
    name = f"qg_bare_{uuid.uuid4().hex[:10]}"
    admin = create_engine(PG_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"CREATE DATABASE {name} TEMPLATE template0"))
    try:
        yield make_url(PG_URL).set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE {name} WITH (FORCE)"))
        admin.dispose()


@pytest.fixture
def bare(bare_url: str) -> Iterator[Db]:
    engine = create_engine(bare_url)
    database = Db(engine, "public")
    database.run(
        "CREATE TABLE orders (id serial PRIMARY KEY, customer_id int)",
        "INSERT INTO orders SELECT g, g % 200 FROM generate_series(1, 20000) g",
        "ANALYZE",
    )
    yield database
    engine.dispose()


def installed(db: Db) -> bool:
    with db.engine.connect() as conn:
        found = conn.exec_driver_sql(
            "SELECT 1 FROM pg_extension WHERE extname = 'hypopg'"
        ).first()
    return found is not None


def test_auto_without_the_extension_suggests_and_says_how_to_verify(bare: Db) -> None:
    outcome = advise(
        bare, "SELECT * FROM orders WHERE customer_id = $1", check_clean=False
    )
    advice = only(outcome).advice
    assert advice is not None and advice.verification is None
    assert advice.create_sql == "CREATE INDEX ON orders (customer_id)"
    assert outcome.advisor_notice is not None
    assert "available but not installed in this database" in outcome.advisor_notice
    assert not installed(bare)


def test_on_creates_the_extension_inside_the_savepoint_only(bare: Db) -> None:
    with bare.engine.connect() as conn:
        outcome = indexes.run_explain(
            conn.connection.dbapi_connection,
            "SELECT * FROM orders WHERE customer_id = $1",
            advise=AdvisorOptions("on", 17),
        )
        assert isinstance(outcome, Explained)
        advice = only(outcome).advice
        assert advice is not None and advice.verification is not None
        assert advice.verification.used is True
        assert outcome.advisor_notice is None
        # rolled back with the savepoint: no extension, nothing hypothetical
        found = conn.exec_driver_sql(
            "SELECT 1 FROM pg_extension WHERE extname = 'hypopg'"
        ).first()
        assert found is None
        conn.rollback()
    assert not installed(bare)


def test_the_probe_is_read_once_per_catalog(bare: Db) -> None:
    catalog = Catalog()
    with bare.engine.connect() as conn:
        for _ in range(2):
            indexes.run_explain(
                conn.connection.dbapi_connection,
                "SELECT * FROM orders WHERE customer_id = $1",
                catalog=catalog,
                advise=AdvisorOptions("auto", 17),
            )
        conn.rollback()
    assert catalog.hypopg == (None, catalog.hypopg[1] if catalog.hypopg else None)
    assert catalog.hypopg is not None and catalog.hypopg[1] is not None


def test_the_installed_extension_is_found_by_the_probe(db: Db) -> None:
    with db.engine.connect() as conn:
        cursor = conn.connection.dbapi_connection.cursor()
        installed_version, available = hypopg.probe(cursor)
        cursor.close()
        conn.rollback()
    assert installed_version is not None and available is not None


# --------------------------------------------------------- the whole test run


def _lines(result: pytest.RunResult) -> list[str]:
    return result.stdout.str().splitlines()


def test_pg_failure_message_shows_the_suggestion_and_its_verification(
    project: pytest.Pytester, with_hypopg: Postgres
) -> None:
    _pg_project(project, with_hypopg).makepyfile(test_a=ORDERS_OF_CUSTOMER)
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "  fix: CREATE INDEX ON orders (customer_id)",
            (
                '  alembic: op.create_index("ix_orders_customer_id", "orders", '
                '["customer_id"])'
            ),
            "  hypopg: the planner uses it (*Scan); estimated cost*",
        ]
    )
    result.stdout.no_fnmatch_line("*index advisor:*")


def test_pg_advisor_off_prints_only_the_old_hint(
    project: pytest.Pytester, with_hypopg: Postgres
) -> None:
    project.makeini("[pytest]\nqueryguard_index_advisor = false\n")
    _pg_project(project, with_hypopg).makepyfile(test_a=ORDERS_OF_CUSTOMER)
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["  fix: CREATE INDEX ON orders (customer_id)"])
    result.stdout.no_fnmatch_line("*alembic:*")
    result.stdout.no_fnmatch_line("*hypopg:*")


def test_pg_hypopg_off_prints_the_suggestion_without_verification(
    project: pytest.Pytester, with_hypopg: Postgres
) -> None:
    project.makeini("[pytest]\nqueryguard_hypopg = off\n")
    _pg_project(project, with_hypopg).makepyfile(test_a=ORDERS_OF_CUSTOMER)
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        ["  fix: CREATE INDEX ON orders (customer_id)", "  alembic:*"]
    )
    result.stdout.no_fnmatch_line("*hypopg:*")
    result.stdout.no_fnmatch_line("*index advisor:*")


def test_pg_missing_extension_prints_the_notice_once(
    project: pytest.Pytester, bare_url: str
) -> None:
    twice = ORDERS_OF_CUSTOMER + ORDERS_OF_CUSTOMER.replace("test_orders", "test_two")
    project.makeconftest(sync_tests.PG_CONFTEST.format(url=bare_url, schema="public"))
    project.makepyfile(test_a=twice)
    result = project.runpytest("--queryguard-index-check", "-p", "no:cacheprovider")
    result.assert_outcomes(failed=2)
    text_out = result.stdout.str()
    assert text_out.count("available but not installed in this database") == 1
    assert "fix: CREATE INDEX ON orders (customer_id)" in text_out
    assert "hypopg:" not in text_out


def test_pg_ignored_table_and_allow_missing_index_still_win(
    project: pytest.Pytester, with_hypopg: Postgres
) -> None:
    source = "import pytest\n" + ORDERS_OF_CUSTOMER.replace(
        "def test_orders", '@pytest.mark.allow_missing_index("orders")\ndef test_orders'
    )
    _pg_project(project, with_hypopg).makepyfile(test_a=source)
    project.runpytest("--queryguard-index-check").assert_outcomes(passed=1)
    other = _pg_project(project, with_hypopg).makepyfile(test_b=ORDERS_OF_CUSTOMER)
    assert other.exists()
    project.makeini("[pytest]\nqueryguard_index_ignore_tables = orders\n")
    project.runpytest("--queryguard-index-check", "test_b.py").assert_outcomes(passed=1)


def test_pg_the_ignore_option_drops_the_finding_with_its_advice(
    project: pytest.Pytester, with_hypopg: Postgres
) -> None:
    project.makeini("[pytest]\nqueryguard_ignore = missing-index orders\n")
    _pg_project(project, with_hypopg).makepyfile(test_a=ORDERS_OF_CUSTOMER)
    project.runpytest("--queryguard-index-check").assert_outcomes(passed=1)


def test_pg_invalid_option_values_are_usage_errors(
    project: pytest.Pytester, with_hypopg: Postgres
) -> None:
    project.makeini("[pytest]\nqueryguard_hypopg = maybe\n")
    project.makepyfile(test_a="def test_a():\n    pass\n")
    result = project.runpytest("--queryguard-index-check")
    assert result.ret != 0
    result.stderr.fnmatch_lines(["*queryguard_hypopg must be one of auto, on, off*"])
