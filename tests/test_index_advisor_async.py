"""The index advisor and hypopg on asyncpg engines, end to end.

Like tests/test_index_advisor.py; the EXPLAIN runs in a DO block there, and
hypopg is driven through the same savepoint.
"""

from __future__ import annotations

import pytest

# pytester drops modules first imported by an inner run from sys.modules when
# that run ends, and importing asyncpg's compiled modules again in a later run
# crashes the interpreter (see tests/test_index_check_async.py).
try:
    import asyncpg  # noqa: F401
    import greenlet  # noqa: F401
    import sqlalchemy.dialects.postgresql.asyncpg
    import sqlalchemy.ext.asyncio  # noqa: F401
except ImportError:  # pragma: no cover - asyncpg is a test dependency
    pass

from tests import test_index_advisor as advisor_tests
from tests import test_index_check_async as async_tests

Postgres = async_tests.Postgres
project = async_tests.project
postgres = async_tests.postgres
pg16 = async_tests.pg16
pg16_async = async_tests.pg16_async
with_hypopg = advisor_tests.with_hypopg
bare_url = advisor_tests.bare_url
needs_pg = async_tests.needs_pg
_async_project = async_tests._async_project
_async_url = async_tests._async_url
ASYNC_ORDERS_OF_CUSTOMER = async_tests.ASYNC_ORDERS_OF_CUSTOMER

pytestmark = [pytest.mark.allow_nplusone, needs_pg]


@pytest.fixture
def hypopg_async(with_hypopg: Postgres) -> Postgres:
    if not async_tests.HAS_ASYNCPG:  # pragma: no cover
        pytest.skip("asyncpg is not installed")
    return Postgres(
        _async_url(with_hypopg.url), with_hypopg.schema, with_hypopg.version
    )


def test_pg_async_failure_shows_the_suggestion_and_its_verification(
    project: pytest.Pytester, hypopg_async: Postgres
) -> None:
    _async_project(project, hypopg_async).makepyfile(test_a=ASYNC_ORDERS_OF_CUSTOMER)
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
    result.stdout.no_fnmatch_line("*could not verify*")
    result.stdout.no_fnmatch_line("*were not checked*")


LEAK_TEST = """
import asyncio

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from qgmodels import Order


async def main(engine):
    async with AsyncSession(engine) as session:
        stmt = select(Order).where(Order.customer_id == 1)
        assert len((await session.scalars(stmt)).all()) == 2
        schema = (await session.execute(text(
            "SELECT extnamespace::regnamespace::text FROM pg_extension "
            "WHERE extname = 'hypopg'"
        ))).scalar()
        count = (await session.execute(text(f"SELECT count(*) FROM {schema}.hypopg()")))
        assert count.scalar() == 0
        seqscan = (await session.execute(text("SHOW enable_seqscan"))).scalar()
        assert seqscan == "on"


def test_orders(engine):
    asyncio.run(main(engine))
"""


def test_pg_async_nothing_is_left_on_the_connection(
    project: pytest.Pytester, hypopg_async: Postgres
) -> None:
    project.makeini("[pytest]\nqueryguard_index_ignore_tables = orders\n")
    _async_project(project, hypopg_async).makepyfile(test_a=LEAK_TEST)
    project.runpytest("--queryguard-index-check").assert_outcomes(passed=1)


def test_pg_async_without_the_extension_suggests_and_prints_the_notice(
    project: pytest.Pytester, bare_url: str
) -> None:
    project.makeconftest(
        async_tests.ASYNC_CONFTEST.format(url=_async_url(bare_url), schema="public")
    )
    project.makepyfile(test_a=ASYNC_ORDERS_OF_CUSTOMER)
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "  fix: CREATE INDEX ON orders (customer_id)",
            "  alembic: *",
            (
                "*index advisor: suggested indexes were not verified*available but "
                "not installed in this database*"
            ),
        ]
    )
    result.stdout.no_fnmatch_line("*  hypopg: *")


def test_pg_async_on_creates_the_extension_in_the_savepoint(
    project: pytest.Pytester, bare_url: str
) -> None:
    project.makeini("[pytest]\nqueryguard_hypopg = on\n")
    project.makeconftest(
        async_tests.ASYNC_CONFTEST.format(url=_async_url(bare_url), schema="public")
    )
    project.makepyfile(test_a=ASYNC_ORDERS_OF_CUSTOMER)
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["  hypopg: the planner uses it (*Scan)*"])
    from sqlalchemy import create_engine

    engine = create_engine(bare_url)
    try:
        with engine.connect() as conn:
            found = conn.exec_driver_sql(
                "SELECT 1 FROM pg_extension WHERE extname = 'hypopg'"
            ).first()
    finally:
        engine.dispose()
    assert found is None


ASYNC_EVENTS = """
import asyncio

from sqlalchemy import text


async def main(engine):
    async with engine.begin() as conn:
        for statement in [
            "CREATE TABLE events (id int, kind text, region text NOT NULL) "
            "PARTITION BY LIST (region)",
            "CREATE TABLE events_eu PARTITION OF events FOR VALUES IN ('eu')",
            "CREATE TABLE events_rest PARTITION OF events DEFAULT",
            "INSERT INTO events SELECT g, 'k' || g % 7, "
            "(ARRAY['eu', 'us'])[1 + g % 2] FROM generate_series(1, 9000) g",
            "ANALYZE events",
        ]:
            await conn.execute(text(statement))
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text("SELECT * FROM events WHERE kind = :k"), {"k": "k1"}
            )
            assert rows.all()
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE events"))


def test_events(engine):
    asyncio.run(main(engine))
"""


def test_pg_async_a_partitioned_table_is_one_verified_finding_on_the_table(
    project: pytest.Pytester, hypopg_async: Postgres
) -> None:
    # #86 on asyncpg: the partition lookup runs through the asyncpg cursor too.
    _async_project(project, hypopg_async).makepyfile(test_a=ASYNC_EVENTS)
    result = project.runpytest("--queryguard-index-check")
    result.assert_outcomes(failed=1)
    out = result.stdout.str()
    assert out.count("is read in full") == 1
    result.stdout.fnmatch_lines(
        [
            "  events is read in full: Seq Scan with filter (events_*.kind = $1)",
            "  fix: CREATE INDEX ON events (kind)",
            '  alembic: op.create_index("ix_events_kind", "events", ["kind"])',
            "  hypopg: the planner uses it (*Scan); estimated cost*",
        ]
    )
    assert "events_eu" not in out and "events_rest" not in out
