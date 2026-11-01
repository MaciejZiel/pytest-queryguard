"""psycopg 3 in async mode (postgresql+psycopg_async), end to end.

Needs QUERYGUARD_PG_URL like tests/test_index_check.py, and psycopg, which is
not a development dependency (it is LGPL-licensed): the psycopg run of the
"postgres" CI job installs it. Elsewhere these tests are skipped.

The N+1 rules work on any driver. The missing-index check supports asyncpg
and sync drivers only, so on psycopg_async it skips the statements with a
notice; these tests pin both.
"""

from __future__ import annotations

import pytest

# pytester drops modules first imported by an inner run from sys.modules when
# that run ends; psycopg's compiled modules are imported here so that they stay
# loaded (as tests/test_index_check_async.py does for asyncpg).
psycopg = pytest.importorskip("psycopg")
pytest.importorskip("greenlet")
import sqlalchemy.dialects.postgresql.psycopg
import sqlalchemy.ext.asyncio  # noqa: F401

from tests import test_index_check as sync_tests

Postgres = sync_tests.Postgres
needs_pg = sync_tests.needs_pg
project = sync_tests.project
postgres = sync_tests.postgres
pg16 = sync_tests.pg16

# pytester runs sessions in-process, so the outer plugin sees their queries too.
pytestmark = [pytest.mark.allow_nplusone, needs_pg]


CONFTEST = """
import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from qgmodels import Base, Customer, Order

URL = {url!r}


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
    engine = create_async_engine(
        URL, connect_args={connect_args!r}, poolclass=NullPool
    )
    asyncio.run(_create(engine))
    yield engine
    asyncio.run(_drop(engine))
"""


def _psycopg_async_project(project: pytest.Pytester, pg: Postgres) -> None:
    from sqlalchemy.engine import make_url

    url = make_url(pg.url).set(drivername="postgresql+psycopg_async")
    rendered = url.render_as_string(hide_password=False)
    project.makeconftest(
        CONFTEST.format(
            url=rendered, connect_args=sync_tests.search_path_args(rendered, pg.schema)
        )
    )


LAZY_LOADS = """
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from qgmodels import Order


def names(session):
    orders = session.scalars(select(Order).order_by(Order.id)).all()
    return [order.customer.name for order in orders]


async def customer_names(engine):
    async with AsyncSession(engine) as session:
        return await session.run_sync(names)


def test_names(engine):
    assert len(asyncio.run(customer_names(engine))) == 6
"""


def test_lazy_loads_on_psycopg_async_are_reported(
    project: pytest.Pytester, postgres: Postgres
) -> None:
    _psycopg_async_project(project, postgres)
    project.makepyfile(test_a=LAZY_LOADS)
    result = project.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "*queryguard: N+1 detected",
            "",
            "  Order.customer was lazy-loaded * times",
            "  first at test_a.py:* in *",
            "  kind: relationship-load  label: Order.customer",
        ]
    )


ORDERS_OF_CUSTOMER = """
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from qgmodels import Order


async def orders_of(engine, customer_id):
    async with AsyncSession(engine) as session:
        stmt = select(Order).where(Order.customer_id == customer_id)
        return (await session.scalars(stmt)).all()


def test_orders(engine):
    assert len(asyncio.run(orders_of(engine, 1))) == 2
"""


def test_the_index_check_skips_psycopg_async_with_a_notice(
    project: pytest.Pytester, pg16: Postgres
) -> None:
    _psycopg_async_project(project, pg16)
    project.makepyfile(test_a=ORDERS_OF_CUSTOMER)
    result = project.runpytest("--queryguard-index-check")
    # orders.customer_id has no index, but the statement is not checked
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(
        [
            # the dialect's driver name is "psycopg" in async mode too
            (
                "statements from the async driver psycopg were not checked: "
                "the index check supports asyncpg and sync drivers only"
            )
        ]
    )
    result.stdout.no_fnmatch_line("*queryguard: missing index*")
