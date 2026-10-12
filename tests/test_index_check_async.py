"""The missing-index check on async engines (asyncpg), end to end.

Like tests/test_index_check.py, these tests need QUERYGUARD_PG_URL. They
reach the same server through asyncpg.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest

# pytester drops modules first imported by an inner run from sys.modules when
# that run ends, and importing asyncpg's compiled modules again in a later run
# crashes the interpreter. Importing them here keeps them loaded.
try:
    import asyncpg  # noqa: F401
    import greenlet  # noqa: F401
    import sqlalchemy.dialects.postgresql.asyncpg
    import sqlalchemy.ext.asyncio  # noqa: F401
except ImportError:  # pragma: no cover - asyncpg is a test dependency
    HAS_ASYNCPG = False
else:
    HAS_ASYNCPG = True

from tests import test_index_check as sync_tests

# The PostgreSQL helpers and fixtures of the sync tests; fixtures are found
# by their names in this module.
PG_URL = sync_tests.PG_URL
Postgres = sync_tests.Postgres
needs_pg = sync_tests.needs_pg
project = sync_tests.project
postgres = sync_tests.postgres
pg16 = sync_tests.pg16
_line = sync_tests._line

# pytester runs sessions in-process, so the outer plugin sees their queries too.
pytestmark = pytest.mark.allow_nplusone


# The same database through asyncpg. NullPool: each asyncio.run() has its own
# event loop, and an asyncpg connection cannot move to another one.
ASYNC_CONFTEST = """
import asyncio

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from qgmodels import Base, Customer, Order

URL = {url!r}
SCHEMA = {schema!r}


def make_engine(**kwargs):
    return create_async_engine(
        URL,
        connect_args={{"server_settings": {{"search_path": SCHEMA}}}},
        poolclass=NullPool,
        **kwargs,
    )


async def _create(engine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine) as session:
        for c in range(1, 4):
            customer = Customer(id=c, name=f"customer {{c}}")
            customer.orders = [Order(id=c * 10 + o) for o in range(2)]
            session.add(customer)
        await session.commit()


async def _drop(engine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
def engine():
    engine = make_engine()
    asyncio.run(_create(engine))
    yield engine
    asyncio.run(_drop(engine))
"""


def _async_url(url: str) -> str:
    from sqlalchemy.engine import make_url

    async_url = make_url(url).set(drivername="postgresql+asyncpg")
    return async_url.render_as_string(hide_password=False)


@pytest.fixture
def pg16_async(pg16: Postgres) -> Postgres:
    if not HAS_ASYNCPG:  # pragma: no cover - asyncpg is a test dependency
        pytest.skip("asyncpg is not installed")
    return Postgres(_async_url(pg16.url), pg16.schema, pg16.version)


def _async_project(project: pytest.Pytester, pg: Postgres) -> pytest.Pytester:
    project.makeconftest(ASYNC_CONFTEST.format(url=pg.url, schema=pg.schema))
    return project


ASYNC_ORDERS_OF_CUSTOMER = """
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from qgmodels import Order


async def orders_of(engine, customer_id):
    async with AsyncSession(engine) as session:
        stmt = select(Order).where(Order.customer_id == customer_id)
        return (await session.scalars(stmt)).all()  # QG_LINE


def test_orders(engine):
    assert len(asyncio.run(orders_of(engine, 1))) == 2
"""


@needs_pg
def test_pg_async_seq_scan_fails_with_table_condition_sql_site_and_hint(
    project: pytest.Pytester, pg16_async: Postgres
) -> None:
    path = _async_project(project, pg16_async).makepyfile(
        test_a=ASYNC_ORDERS_OF_CUSTOMER
    )
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "*queryguard: missing index",
            "",
            "  orders is read in full: Seq Scan with filter (orders.customer_id = $1)",
            f"  first at test_a.py:{_line(path)} in orders_of",
            (
                "  sql: SELECT orders.id, orders.customer_id FROM orders "
                "WHERE orders.customer_id = $1::INTEGER"
            ),
            "  fix: CREATE INDEX ON orders (customer_id)",
        ]
    )
    result.stdout.no_fnmatch_line("*were not checked*")


@needs_pg
def test_pg_async_index_created_in_the_test_transaction_is_seen(
    project: pytest.Pytester, pg16_async: Postgres
) -> None:
    _async_project(project, pg16_async).makepyfile(
        test_a="""
        import asyncio

        from sqlalchemy import select, text
        from sqlalchemy.ext.asyncio import AsyncSession

        from qgmodels import Order


        async def main(engine):
            async with AsyncSession(engine) as session:
                await session.execute(text("CREATE INDEX ON orders (customer_id)"))
                stmt = select(Order).where(Order.customer_id == 1)
                assert len((await session.scalars(stmt)).all()) == 2
                await session.rollback()


        def test_orders(engine):
            asyncio.run(main(engine))
        """
    )
    project.runpytest("--queryguard-index-check").assert_outcomes(passed=1)


@needs_pg
def test_pg_async_temporary_table_reports_the_trailing_column(
    project: pytest.Pytester, pg16_async: Postgres
) -> None:
    # EXPLAIN VERBOSE says "pg_temp"; pg_namespace has "pg_temp_<n>".
    if pg16_async.version >= (18,):
        pytest.skip("PostgreSQL 18 can skip-scan the leading column")
    _async_project(project, pg16_async).makepyfile(
        test_a="""
        import asyncio

        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import AsyncSession


        async def main(engine):
            async with AsyncSession(engine) as session:
                for sql in [
                    "CREATE TEMPORARY TABLE tmp_items (id int PRIMARY KEY, "
                    "tenant_id int, status text)",
                    "CREATE INDEX ON tmp_items (tenant_id, status)",
                    "INSERT INTO tmp_items SELECT g, g % 3, 's' || g % 4 "
                    "FROM generate_series(1, 30) g",
                ]:
                    await session.execute(text(sql))
                sql = "SELECT * FROM tmp_items WHERE status = :s"
                await session.execute(text(sql), {"s": "s1"})
                await session.rollback()


        def test_temp(engine):
            asyncio.run(main(engine))
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
def test_pg_async_temporary_table_with_a_varchar_column_gets_the_index_hint(
    project: pytest.Pytester, pg16_async: Postgres
) -> None:
    _async_project(project, pg16_async).makepyfile(
        test_a="""
        import asyncio

        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import AsyncSession


        async def main(engine):
            async with AsyncSession(engine) as session:
                for sql in [
                    "CREATE TEMPORARY TABLE tmp_casts (id int PRIMARY KEY, "
                    "label varchar(20))",
                    "INSERT INTO tmp_casts SELECT g, 'l' || g FROM "
                    "generate_series(1, 20) g",
                ]:
                    await session.execute(text(sql))
                sql = "SELECT * FROM tmp_casts WHERE label::text = :s"
                await session.execute(text(sql), {"s": "x"})
                await session.rollback()


        def test_temp(engine):
            asyncio.run(main(engine))
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
def test_pg_async_check_leaves_the_transaction_and_settings_alone(
    project: pytest.Pytester, pg16_async: Postgres
) -> None:
    _async_project(project, pg16_async).makepyfile(
        test_a="""
        import asyncio

        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import AsyncSession

        from conftest import SCHEMA
        from qgmodels import Customer


        async def main(engine):
            async with AsyncSession(engine) as session:
                session.add(Customer(id=9, name="new"))
                await session.flush()
                # a table created in this open transaction is visible to the check
                await session.execute(text("CREATE TABLE scratch (a int)"))
                await session.execute(
                    text("SELECT a FROM scratch WHERE a = :a"), {"a": 1}
                )
                # planning this again with search_path moved away fails
                await session.execute(
                    text(
                        "SELECT set_config('search_path', 'nowhere', true), "
                        "count(*) FROM orders"
                    )
                )
                await session.execute(
                    text("SELECT set_config('search_path', :schema, true)"),
                    {"schema": SCHEMA},
                )
                show = text("SHOW enable_seqscan")
                assert (await session.execute(show)).scalar() == "on"
                show = text("SHOW statement_timeout")
                assert (await session.execute(show)).scalar() == "0"
                name = text("SELECT name FROM customers WHERE id = 9")
                assert (await session.execute(name)).scalar_one() == "new"
                assert session.in_transaction()
                await session.rollback()
                assert await session.get(Customer, 9) is None


        def test_state(engine):
            asyncio.run(main(engine))
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
    result.stdout.no_fnmatch_line("*AssertionError*")


@needs_pg
def test_pg_async_autocommit_connection(
    project: pytest.Pytester, pg16_async: Postgres
) -> None:
    _async_project(project, pg16_async).makepyfile(
        test_a="""
        import asyncio

        from sqlalchemy import select, text

        from conftest import make_engine
        from qgmodels import Order


        async def main():
            auto = make_engine(isolation_level="AUTOCOMMIT")
            try:
                async with auto.connect() as conn:
                    stmt = select(Order.id).where(Order.customer_id == 1)
                    assert len((await conn.execute(stmt)).all()) == 2
                    show = text("SHOW enable_seqscan")
                    assert (await conn.execute(show)).scalar() == "on"
            finally:
                await auto.dispose()


        def test_autocommit(engine):
            asyncio.run(main())
        """
    )
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["  orders is read in full: Seq Scan with filter*"])
    result.stdout.no_fnmatch_line("*AssertionError*")


@needs_pg
def test_pg_async_manual_transaction_on_an_autocommit_connection_survives_the_check(
    project: pytest.Pytester, pg16_async: Postgres
) -> None:
    _async_project(project, pg16_async).makepyfile(
        test_a="""
        import asyncio

        from sqlalchemy import text

        from conftest import make_engine


        async def main():
            auto = make_engine(isolation_level="AUTOCOMMIT")
            try:
                async with auto.connect() as conn:
                    await conn.exec_driver_sql("BEGIN")
                    await conn.exec_driver_sql("INSERT INTO orders VALUES (100, 1)")
                    sql = "SELECT id FROM orders WHERE customer_id = $1"
                    assert len((await conn.exec_driver_sql(sql, (1,))).all()) == 3
                    await conn.exec_driver_sql("COMMIT")
                async with auto.connect() as conn:
                    sql = text("SELECT count(*) FROM orders WHERE id = 100")
                    count = (await conn.execute(sql)).scalar()
                assert count == 1, f"the committed row is gone: count={count}"
            finally:
                await auto.dispose()


        def test_manual_begin(engine):
            asyncio.run(main())
        """
    )
    project.makeini("[pytest]\nqueryguard_index_ignore_tables = orders\n")
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(passed=1)
    result.stdout.no_fnmatch_line("*the index check stopped*")


@needs_pg
def test_pg_async_joinedload_reports_the_joined_table(
    project: pytest.Pytester, pg16_async: Postgres
) -> None:
    _async_project(project, pg16_async).makepyfile(
        test_a="""
        import asyncio

        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy.orm import joinedload

        from qgmodels import Customer


        async def main(engine):
            async with AsyncSession(engine) as session:
                stmt = (
                    select(Customer)
                    .options(joinedload(Customer.orders))
                    .where(Customer.id == 1)
                )
                result = await session.scalars(stmt)
                assert len(result.unique().one().orders) == 2


        def test_customer(engine):
            asyncio.run(main(engine))
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


# The DO block must give the plan a plain EXPLAIN gives: compare both, on
# the same tables, for statements of every shape the rules look at.
EQUAL_PLANS_SETUP = [
    "CREATE TABLE owners (id int PRIMARY KEY, name text)",
    (
        "CREATE TABLE items (id serial PRIMARY KEY, owner_id int, tenant_id int, "
        "status text, label varchar(20), total numeric, deleted_at timestamp)"
    ),
    "CREATE INDEX items_tenant_status ON items (tenant_id, status)",
    "CREATE INDEX items_live ON items (id) WHERE deleted_at IS NULL",
    "INSERT INTO owners SELECT g, 'o' || g FROM generate_series(1, 5) g",
    (
        "INSERT INTO items (owner_id, tenant_id, status, label, total) "
        "SELECT g % 5 + 1, g % 3, 's' || (g % 4), 'l' || g, g "
        "FROM generate_series(1, 30) g"
    ),
]
EQUAL_PLANS_STATEMENTS = [
    "SELECT * FROM items WHERE owner_id = $1",
    "SELECT * FROM items WHERE owner_id = $1 ORDER BY id LIMIT 10",
    "SELECT * FROM items WHERE status = $1",
    "SELECT * FROM items WHERE tenant_id = $1 AND status = $2",
    (
        "SELECT o.name, i.id FROM owners o JOIN items i ON i.owner_id = o.id "
        "WHERE o.id = $1"
    ),
    "SELECT * FROM items WHERE id = ANY($1::int[])",
    "SELECT * FROM items WHERE label = $1::varchar",
    "SELECT * FROM items WHERE label LIKE 'x%' AND owner_id = $1",
    "SELECT '$qg_sql$' AS t, $$q$$ AS u FROM items WHERE owner_id = $1",
    (
        "SELECT o.id, (SELECT count(*) FROM items i WHERE i.owner_id = o.id) "
        "FROM owners o"
    ),
    "WITH x AS (SELECT * FROM items WHERE total > $1) SELECT * FROM x",
    "SELECT id FROM items WHERE deleted_at IS NULL AND label = $1",
    "/* tagged */ SELECT id FROM items WHERE total = $1",
]


@needs_pg
def test_pg_async_plans_equal_plain_explain_plans(pg16_async: Postgres) -> None:
    import asyncio

    from sqlalchemy import create_engine, text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from pytest_queryguard.indexes import Explained, find_scans, run_explain

    assert PG_URL is not None
    schema = pg16_async.schema
    sync = create_engine(
        PG_URL, connect_args={"startup_params": {"search_path": schema}}
    )
    with sync.begin() as conn:
        for statement in EQUAL_PLANS_SETUP:
            conn.execute(text(statement))

    def sync_plans() -> list[object]:
        with sync.connect() as conn:
            conn.execute(text("SELECT 1"))  # opens the transaction
            raw = conn.connection.dbapi_connection
            return [run_explain(raw, sql) for sql in EQUAL_PLANS_STATEMENTS]

    async def async_plans() -> list[object]:
        engine = create_async_engine(
            pg16_async.url,
            connect_args={"server_settings": {"search_path": schema}},
            poolclass=NullPool,
        )
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
                return await conn.run_sync(
                    lambda sync_conn: [
                        run_explain(
                            sync_conn.connection.dbapi_connection, sql, asyncpg=True
                        )
                        for sql in EQUAL_PLANS_STATEMENTS
                    ]
                )
        finally:
            await engine.dispose()

    try:
        expected, got = sync_plans(), asyncio.run(async_plans())
    finally:
        sync.dispose()
    for sql, plain, in_block in zip(EQUAL_PLANS_STATEMENTS, expected, got, strict=True):
        assert isinstance(plain, Explained), (sql, plain)
        assert in_block == plain, sql
    findings = [
        find_scans(e.plan, e.current_schema)
        for e in expected
        if isinstance(e, Explained)
    ]
    assert any(findings), "the statements should include missing indexes"


@pytest.fixture
def pg16_async_without_plpgsql(pg16: Postgres) -> Iterator[Postgres]:
    """A throwaway database without PL/pgSQL, with a fresh schema in it."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    if not HAS_ASYNCPG:  # pragma: no cover - asyncpg is a test dependency
        pytest.skip("asyncpg is not installed")
    name = f"qg_noplpgsql_{uuid.uuid4().hex[:12]}"
    admin = create_engine(pg16.url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f"CREATE DATABASE {name}"))
        sync_url = make_url(pg16.url).set(database=name)
        scratch = create_engine(sync_url, isolation_level="AUTOCOMMIT")
        try:
            with scratch.connect() as conn:
                conn.execute(text("DROP EXTENSION plpgsql"))
                conn.execute(text(f"CREATE SCHEMA {pg16.schema}"))
        finally:
            scratch.dispose()
        url = _async_url(sync_url.render_as_string(hide_password=False))
        yield Postgres(url, pg16.schema, pg16.version)
    finally:
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
        admin.dispose()


@needs_pg
def test_pg_async_without_plpgsql_gives_one_notice_not_one_error_per_statement(
    project: pytest.Pytester, pg16_async_without_plpgsql: Postgres
) -> None:
    _async_project(project, pg16_async_without_plpgsql).makepyfile(
        test_a="""
        import asyncio

        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession

        from qgmodels import Customer, Order


        async def main(engine):
            async with AsyncSession(engine) as session:
                for stmt in (
                    select(Order).where(Order.customer_id == 1),
                    select(Customer).where(Customer.id == 1),
                    select(Customer).where(Customer.name == "x"),
                ):
                    await session.scalars(stmt)


        def test_statements(engine):
            asyncio.run(main(engine))
        """
    )
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(passed=1)
    notice = "asyncpg statements were not checked: PL/pgSQL is not installed"
    result.stdout.fnmatch_lines([f"*{notice}*"])
    assert result.stdout.str().count(notice) == 1
    result.stdout.no_fnmatch_line("*could not be explained*")
    result.stdout.no_fnmatch_line('*language "plpgsql" does not exist*')


@needs_pg
def test_pg_async_catalog_lookups_are_one_prepared_statement(
    pg16_async: Postgres,
) -> None:
    """Lookups with different names must not fill asyncpg's statement cache."""
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from pytest_queryguard import indexes
    from pytest_queryguard.indexes import Catalog, Explained, run_explain

    async def main() -> list[str]:
        engine = create_async_engine(
            pg16_async.url,
            connect_args={"server_settings": {"search_path": pg16_async.schema}},
            poolclass=NullPool,
        )
        catalog = Catalog()
        try:
            async with engine.connect() as conn:
                for n in range(4):
                    await conn.execute(
                        text(f"CREATE TABLE t{n} (a int, b text, c varchar)")
                    )
                    await conn.execute(text(f"CREATE INDEX t{n}_ab ON t{n} (a, b)"))

                def explain(sync_conn: Any, n: int) -> None:
                    sql = f"SELECT * FROM t{n} WHERE a = $1 AND c::text = $2"
                    outcome = run_explain(
                        sync_conn.connection.dbapi_connection,
                        sql,
                        leading=True,
                        catalog=catalog,
                        asyncpg=True,
                    )
                    assert isinstance(outcome, Explained)
                    assert outcome.catalog_error is None
                    assert outcome.leading and outcome.varchar

                for n in range(4):
                    await conn.run_sync(explain, n)
                rows = await conn.execute(
                    text("SELECT statement FROM pg_prepared_statements")
                )
                return [row[0] for row in rows]
        finally:
            await engine.dispose()

    prepared = asyncio.run(main())
    # Explaining four tables prepared exactly the two catalog lookups, once
    # each: names are parameters, so they never reach the statement text.
    lookups = [
        statement
        for statement in prepared
        if "FROM pg_index" in statement or "t.typname = 'varchar'" in statement
    ]
    assert sorted(lookups) == sorted(
        [
            indexes._LEADING_LOOKUP.format(names=indexes._NAMES_ARRAYS),
            indexes._VARCHAR_LOOKUP.format(names=indexes._NAMES_ARRAYS),
        ]
    )


ASYNC_PARTIAL = """
import asyncio

from sqlalchemy import column, select, table, text
from sqlalchemy.ext.asyncio import AsyncSession

t1 = table("t1", column("status"), column("x"))


async def run(engine):
    async with AsyncSession(engine) as session:
        for sql in (
            "CREATE TABLE t1 (status varchar, x int)",
            "CREATE INDEX t1_partial ON t1 (x) WHERE status = 'open'",
            "INSERT INTO t1 SELECT CASE WHEN g % 50 = 0 THEN 'open' ELSE 'done' END, g "
            "FROM generate_series(1, 5000) g",
            "ANALYZE t1",
        ):
            await session.execute(text(sql))
        stmt = select(t1).where(t1.c.status == "open", t1.c.x == 10)
        await session.execute(stmt)


def test_open(engine):
    asyncio.run(run(engine))
"""


@needs_pg
def test_pg_async_a_partial_index_that_may_serve_the_statement_is_not_reported(
    project: pytest.Pytester, pg16_async: Postgres
) -> None:
    _async_project(project, pg16_async).makepyfile(test_a=ASYNC_PARTIAL)
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(
        ["1 statement skipped: a partial index on t1 may serve it; *"]
    )
    result.stdout.no_fnmatch_line("*missing index*")
