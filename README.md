# pytest-queryguard

[![CI](https://github.com/MaciejZiel/pytest-queryguard/actions/workflows/ci.yml/badge.svg)](https://github.com/MaciejZiel/pytest-queryguard/actions/workflows/ci.yml)

A pytest plugin that fails tests with N+1 queries and enforces query budgets
for SQLAlchemy 2, in sync, async and FastAPI code. On PostgreSQL 16 and newer
it can also fail tests whose queries have no index to use.

## Why

ORM code that looks correct can send one query per row instead of one query
per page:

```python
orders = session.scalars(select(Order)).all()  # 1 query
for order in orders:
    print(order.customer.name)  # +1 query per order
```

The tests pass. A test database holds a handful of rows, so 5 extra queries
cost nothing. In production the same endpoint loads 500 orders, sends 501
queries and gets slow. Nothing in the test run warned you.

pytest-queryguard watches the SQL each test sends. When it sees the N+1
pattern, it fails the test and points to the line that caused it, even if
the test database has only a few rows.

## Before and after

The code below is a small example project. All output in this section is
copied from a real run.

`app/orders.py`:

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Order


def list_orders(session: Session) -> list[dict[str, object]]:
    orders = session.scalars(select(Order)).all()
    return [{"id": order.id, "customer": order.customer.name} for order in orders]
```

`app/customers.py`:

```python
from sqlalchemy.orm import Session

from app.models import Customer


def fetch_customers(session: Session, ids: list[int]) -> list[Customer]:
    customers = []
    for customer_id in ids:
        customers.append(session.get(Customer, customer_id))
    return customers


def rename_customers(session: Session, names: dict[int, str]) -> None:
    for customer_id, name in names.items():
        session.get(Customer, customer_id).name = name
        session.flush()
```

`tests/test_orders.py` (the `session` fixture in `tests/conftest.py`
creates 50 customers with one order each; `tests/__init__.py` exists, so
pytest puts the project root on `sys.path` and `app` can be imported):

```python
import pytest

from app.customers import fetch_customers, rename_customers
from app.orders import list_orders


def test_list_orders(session):
    assert len(list_orders(session)) == 50


def test_fetch_customers(session):
    assert len(fetch_customers(session, list(range(1, 51)))) == 50


@pytest.mark.max_queries(2)
def test_rename_customers(session):
    rename_customers(session, {1: "Ada", 2: "Grace"})
```

All three tests pass without the plugin. With it installed,
`pytest --queryguard-report` gives the output below. It comes from Python
3.13 with SQLAlchemy 2.1; Python 3.12 prints the same. Older versions print
slightly different SQL or function names, for example `in <listcomp>` on
Python 3.10 and 3.11, or column aliases in the SQL on SQLAlchemy 2.0.

```
tests/test_orders.py FFF                                                 [100%]

=================================== FAILURES ===================================
_______________________________ test_list_orders _______________________________
queryguard: N+1 detected

  Order.customer was lazy-loaded 50 times
  first at app/orders.py:9 in list_orders
  sql: SELECT customer.id, customer.name FROM customer WHERE customer.id = ?
  fix: load it with the parent query, e.g.
       select(Order).options(selectinload(Order.customer))
_____________________________ test_fetch_customers _____________________________
queryguard: N+1 detected

  the same statement ran 50 times
  first at app/customers.py:9 in fetch_customers
  SELECT customer.id, customer.name FROM customer WHERE customer.id = ?
  fix: fetch all rows in one query (WHERE id IN (...)) instead of in a loop
____________________________ test_rename_customers _____________________________
queryguard: expected at most 2 queries, got 4

  1. SELECT customer.id, customer.name FROM customer WHERE customer.id = ?  (app/customers.py:15 in rename_customers)
  2. UPDATE customer SET name=? WHERE customer.id = ?  (app/customers.py:16 in rename_customers)
  3. SELECT customer.id, customer.name FROM customer WHERE customer.id = ?  (app/customers.py:15 in rename_customers)
  4. UPDATE customer SET name=? WHERE customer.id = ?  (app/customers.py:16 in rename_customers)
============================== queryguard report ===============================
   51  tests/test_orders.py::test_list_orders
   50  tests/test_orders.py::test_fetch_customers
    4  tests/test_orders.py::test_rename_customers
=========================== short test summary info ============================
FAILED tests/test_orders.py::test_list_orders - Failed: queryguard: N+1 detected
FAILED tests/test_orders.py::test_fetch_customers - Failed: queryguard: N+1 d...
FAILED tests/test_orders.py::test_rename_customers - Failed: queryguard: expe...
============================== 3 failed in 0.07s ===============================
```

The fixed code loads related rows with the parent query and fetches many
rows with one `IN` query.

`app/orders.py`, fixed:

```python
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Order


def list_orders(session: Session) -> list[dict[str, object]]:
    orders = session.scalars(select(Order).options(selectinload(Order.customer))).all()
    return [{"id": order.id, "customer": order.customer.name} for order in orders]
```

`app/customers.py`, fixed:

```python
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Customer


def fetch_customers(session: Session, ids: list[int]) -> Sequence[Customer]:
    return session.scalars(select(Customer).where(Customer.id.in_(ids))).all()


def rename_customers(session: Session, names: dict[int, str]) -> None:
    customers = session.scalars(select(Customer).where(Customer.id.in_(names)))
    for customer in customers:
        customer.name = names[customer.id]
    session.flush()
```

The same tests, unchanged:

```
tests/test_orders.py ...                                                 [100%]

============================== queryguard report ===============================
    2  tests/test_orders.py::test_list_orders
    2  tests/test_orders.py::test_rename_customers
    1  tests/test_orders.py::test_fetch_customers
============================== 3 passed in 0.04s ===============================
```

## Install

```bash
pip install pytest-queryguard
```

It needs Python 3.10 or newer, pytest 8 or newer and SQLAlchemy 2.0 or
newer. The plugin registers itself with pytest. There is nothing to import
or configure.

## Usage

### Automatic N+1 detection

Every test is checked. A test fails when, inside the test function:

- one relationship (for example `Order.customer`) is lazy-loaded for 2 or
  more different parent objects, or
- one `SELECT`, with its parameters and literals removed, runs 5 or more
  times.

Both thresholds can be changed (see [Configuration](#configuration)).

### Query budgets with the `max_queries` marker

```python
import pytest


@pytest.mark.max_queries(3)
def test_order_page(client):
    client.get("/orders/1")


@pytest.mark.max_queries(n=3)  # the same, with a keyword
def test_order_page_again(client):
    client.get("/orders/1")
```

The test fails when its body runs more SQL statements than the limit. The
failure lists every statement with the line that issued it. Budgets count
all statements, writes included, and they always fail, whatever
`queryguard_mode` is set to. If the test already fails on its own, an
exceeded budget is not reported, because the original failure wins.

### Query budgets with the `max_queries()` context manager

```python
from pytest_queryguard import max_queries


def test_checkout(service, cart):
    with max_queries(2):
        service.checkout(cart)
```

Only the statements inside the `with` block count. Above the limit it raises
`QueryBudgetExceeded`, a subclass of `AssertionError`. The context manager
does not depend on pytest, so it also works in a script or a benchmark. It
checks the budget only; N+1 detection is done by the plugin around the
whole test.

### Reading the queries in a test

`record_queries()` records the SQL statements a block sends and lets the
test read them back:

```python
from pytest_queryguard import record_queries

from app.customers import fetch_customers
from app.orders import list_orders


def test_list_orders_loads_customers_with_the_orders(session):
    with record_queries() as log:
        list_orders(session)
    assert log.count == 2
    assert log.queries[1].sql.startswith("SELECT customer.id")
    assert log.queries[1].relationship == "Order.customer"


def test_fetch_customers(session):
    with record_queries() as log:
        fetch_customers(session, [1, 2, 3])
    log.assert_no_nplusone(repeat_threshold=2)
```

`log.queries` is a tuple of `Query` objects, in the order the statements
ran. Each has:

- `sql`: the statement as SQLAlchemy sent it to the driver;
- `callsite`: a `CallSite` with `filename`, `lineno` and `function`, the
  first frame outside SQLAlchemy, pytest, the standard library,
  `site-packages` and the plugin, or `None`;
- `relationship`: the relationship the statement loaded, lazily or as the
  extra query of `selectinload`, such as `"Order.customer"`, or `None`.

`log.count` is the number of statements. Both grow inside the block and
stay fixed after it. Statements from other threads are recorded too.

`log.assert_no_nplusone()` applies the plugin's two N+1 rules to the
block. It raises `NPlusOneDetected`, a subclass of `AssertionError`, with
the plugin's message. The thresholds are keyword arguments,
`relationship_threshold=2` and `repeat_threshold=5` by default, the same
defaults as the ini options. It does not read the ini options. With the
`fetch_customers` loop from [Before and after](#before-and-after), the
plugin accepts 3 lookups, because its threshold is 5, while the stricter
call above does not (`pytest --tb=short`, Python 3.13, SQLAlchemy 2.1):

```
tests/test_queries.py .F                                                 [100%]

=================================== FAILURES ===================================
_____________________________ test_fetch_customers _____________________________
tests/test_queries.py:18: in test_fetch_customers
    log.assert_no_nplusone(repeat_threshold=2)
E   pytest_queryguard.api.NPlusOneDetected: queryguard: N+1 detected
E   
E     the same statement ran 3 times
E     first at app/customers.py:9 in fetch_customers
E     SELECT customer.id, customer.name FROM customer WHERE customer.id = ?
E     fix: fetch all rows in one query (WHERE id IN (...)) instead of in a loop
=========================== short test summary info ============================
FAILED tests/test_queries.py::test_fetch_customers - pytest_queryguard.api.NP...
========================= 1 failed, 1 passed in 0.05s ==========================
```

Recording changes nothing else. The plugin's N+1 detection, budgets and
index check see the same statements, and `record_queries()` combines with
`max_queries()`: `with max_queries(3), record_queries() as log:`. Like
`max_queries()`, it also works outside pytest.

### Opting out

```python
@pytest.mark.allow_nplusone
def test_legacy_report(session): ...
```

The test is not checked for N+1. A `max_queries` marker on the same test is
still enforced.

### Async and FastAPI

Nothing changes for async code. `AsyncSession` and `create_async_engine` run
on a sync engine underneath, which the plugin already watches. On SQLAlchemy
2.1 and newer, async support needs `pip install "sqlalchemy[asyncio]"`,
which provides greenlet. The failure
points to the `await` in your code, not to SQLAlchemy internals:

```python
# app/orders.py; the models use SQLAlchemy's AsyncAttrs mixin.
async def customer_names(session: AsyncSession) -> list[str]:
    orders = (await session.scalars(select(Order))).all()
    return [(await order.awaitable_attrs.customer).name for order in orders]
```

```python
# tests/test_async.py; main() creates the engine and data, then calls
# customer_names(session).
def test_customer_names():
    asyncio.run(main())
```

This fails with `Order.customer was lazy-loaded 3 times`, first at the line
with the `await`. pytest cannot run `async def` tests by itself. With
pytest-asyncio or anyio you can write the test as `async def` instead of
calling `asyncio.run`.

FastAPI's `TestClient` runs the application in another thread. Queries from
any thread are counted, so an N+1 inside an endpoint fails the test that
called it:

```python
def test_orders_endpoint(client):
    assert client.get("/orders").status_code == 200
```

### Session report

`pytest --queryguard-report` prints, at the end of the run, the 10 tests
that ran the most queries, failing tests included. See the output in
[Before and after](#before-and-after).

### Missing indexes (PostgreSQL 16 and newer)

A test database holds a few rows, so a query that reads the whole table is
as fast in CI as one that uses an index. The missing index shows up in
production, once the table has grown. With the index check on, every
`SELECT` in a test body is planned again by PostgreSQL with sequential scans
switched off. If the plan still reads a whole table, or a whole index, to
apply the statement's filter, no index can serve that filter, however small
the table, and the test fails.

The check is off by default. Turn it on for a run:

```bash
pytest --queryguard-index-check
```

or for the project, with `queryguard_index_check = true` (see
[Configuration](#configuration)).

The example below uses a real PostgreSQL 17 server through pg8000. All
output is copied from a run with Python 3.13, SQLAlchemy 2.1 and pytest 9.1.

`app/models.py`:

```python
class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"))
```

`app/orders.py`:

```python
def orders_of(session: Session, customer_id: int) -> Sequence[Order]:
    stmt = select(Order).where(Order.customer_id == customer_id)
    return session.scalars(stmt).all()
```

`tests/test_orders.py` (the `session` fixture creates the tables in
PostgreSQL and adds 6 orders):

```python
def test_orders_of(session):
    assert len(orders_of(session, 1)) == 3
```

`pytest --queryguard-index-check`:

```
tests/test_orders.py F                                                   [100%]

=================================== FAILURES ===================================
________________________________ test_orders_of ________________________________
queryguard: missing index

  orders is read in full: Seq Scan with filter (orders.customer_id = $1)
  first at app/orders.py:11 in orders_of
  sql: SELECT orders.id, orders.customer_id FROM orders WHERE orders.customer_id = %s::INTEGER
  fix: CREATE INDEX ON orders (customer_id)

  a sequential scan is fine on a small lookup table:
  list it in queryguard_index_ignore_tables
=========================== short test summary info ============================
FAILED tests/test_orders.py::test_orders_of - Failed: queryguard: missing index
============================== 1 failed in 0.27s ===============================
```

With `mapped_column(ForeignKey("customer.id"), index=True)` the same test
passes:

```
tests/test_orders.py .                                                   [100%]

============================== 1 passed in 0.19s ===============================
```

What is reported:

- a sequential scan with a filter: the `WHERE` condition has no index.
  Correlated subqueries and `EXISTS` show up here too, through the filter
  of the subquery's scan;
- a scan of a whole index with a filter that is an equality (`=` or
  `IN`) on one column and no index condition. The planner picks it for
  `WHERE customer_id = ? ORDER BY id LIMIT 10` to avoid the sequential scan.
  Pagination with a range or `IS NULL` filter (`WHERE total > 0 ORDER BY id
  LIMIT 20`) walks the primary key on purpose and is not reported. A whole
  index read as a bitmap, which PostgreSQL may choose before `ANALYZE` for
  an `INCLUDE` index, counts the same way;
- an index condition, an equality on one column, that leaves out the first
  column of a multicolumn btree index: with only an index on
  `(tenant_id, status)`, `WHERE status = ?` reads the whole index on
  PostgreSQL 16 and 17. PostgreSQL 18 can skip-scan such an index, so this
  case is not checked there. BRIN and GIN indexes serve any of their
  columns and are not judged this way.

Not reported: a sequential scan without a filter, such as `SELECT * FROM t`,
`SELECT count(*) FROM t` or a join without a `WHERE`, because the statement
reads the whole table anyway; scans of PostgreSQL's own catalogs. Whether a
join reads its unindexed side in full depends on table statistics, so join
conditions on their own are never reported.

The `fix:` line names a column only when the condition compares one plain
column with `=`, `<`, `<=`, `>` or `>=`. PostgreSQL prints a `::text` cast
for `varchar` columns compared with text, which an index on the column
still serves, so the check reads the column types from the catalog and
accepts that cast for `varchar` columns only. A column inside a function or
another cast, such as `total::int = ?`, or `id::text = ?` on an integer,
enum or `char(n)` column, gets
`add an index that matches this condition (an expression index may be needed)`,
because an index on the plain column would not help there.

Each distinct statement is explained once per test, inside a savepoint on
the connection that ran it, so the check sees tables and rows the test has
not committed yet and leaves the test's transaction as it was. Statements
from fixtures are not checked, like for N+1 detection. Statements that
start with comments, such as sqlcommenter tags, are checked too.

The check costs a few database round trips per distinct statement per test:
open the savepoint, set the planner settings, `EXPLAIN`, read from the
catalog the first column of the btree indexes the plan uses and the
`varchar` columns of tables cast to text (once per index or table per
test), and roll back. With pg8000 against PostgreSQL 17 in a local Docker
container, 100 tests with 10 distinct statements each went from 2.2–2.4 s
to 3.9–7.3 s over three runs, about 2–5 ms per statement. Most of that is round-trip time, so it is lower
with a faster connection to the database.

A sequential scan is the right plan for a small lookup table, also in
production. Exclude such tables with `queryguard_index_ignore_tables`, one
test with `@pytest.mark.allow_missing_index`, or only some tables in one
test with `@pytest.mark.allow_missing_index("countries", "currencies")`.
Findings follow `queryguard_mode`: `warn` turns them into warnings and
`off` turns the check off. `allow_nplusone` does not affect the index check.

Statements that cannot be checked do not fail the test. They are summed up
once at the end of the session, for example with SQLite:

```
============================ queryguard index check ============================
sqlite statements were not checked: the index check needs PostgreSQL 16 or newer
```

The same summary names a PostgreSQL server older than 16, async engines,
and the number of statements PostgreSQL could not plan, with the first
error and its SQL.

## Configuration

| Option | Values | Default | Meaning |
|---|---|---|---|
| `queryguard_mode` | `fail`, `warn`, `off` | `fail` | `fail` fails a test with N+1. `warn` reports it as a `QueryGuardWarning` and lets the test pass. `off` turns detection off; tests without a budget are not measured. |
| `queryguard_relationship_threshold` | integer ≥ 2 | `2` | Number of different parent objects for which one relationship must be lazy-loaded to count as N+1. |
| `queryguard_repeat_threshold` | integer ≥ 2 | `5` | Number of times one normalized `SELECT` must run to count as N+1. |
| `queryguard_index_check` | `true`, `false` | `false` | Checks each `SELECT` for a missing index (PostgreSQL 16 or newer). |
| `queryguard_index_ignore_tables` | table names or `fnmatch` patterns, one per line | empty | Tables the index check ignores. A pattern matches `table` or `schema.table`, for example `countries`, `lookup_*` or `audit.*`. |

Any invalid value stops pytest with a usage error.

In `pyproject.toml`:

```toml
[tool.pytest.ini_options]
queryguard_mode = "warn"
queryguard_relationship_threshold = 3
queryguard_repeat_threshold = 10
queryguard_index_check = true
queryguard_index_ignore_tables = ["countries", "lookup_*"]
```

With pytest 9 you can use the native TOML table instead:

```toml
[tool.pytest]
queryguard_mode = "warn"
queryguard_relationship_threshold = 3
queryguard_repeat_threshold = 10
queryguard_index_check = true
queryguard_index_ignore_tables = ["countries", "lookup_*"]
```

`pytest.ini` and `setup.cfg` work as usual.

The warning classes, `pytest_queryguard.QueryGuardWarning` for N+1 and
missing indexes in `warn` mode and
`pytest_queryguard.QueryGuardInternalWarning` for an internal error, work
with pytest's usual warning filters. For example, to turn these warnings
back into failures for one job:

```toml
[tool.pytest.ini_options]
queryguard_mode = "warn"
filterwarnings = ["error::pytest_queryguard.QueryGuardWarning"]
```

Command-line options:

| Option | Meaning |
|---|---|
| `--queryguard-mode=fail\|warn\|off` | Overrides `queryguard_mode` for this run. |
| `--queryguard-report` | Prints the 10 tests with the most queries at the end of the session. |
| `--queryguard-index-check` | Turns the missing-index check on for this run. |
| `--no-queryguard-index-check` | Turns the missing-index check off for this run, whatever `queryguard_index_check` says. |

## How it works

1. When pytest starts (unless `queryguard_mode = off`; budgets still install
   the listeners when a test needs them), the plugin adds two class-level
   SQLAlchemy listeners:
   `do_orm_execute` on `Session` sees relationship lazy loads, and
   `before_cursor_execute` on `Engine` sees every SQL statement.
2. Measurement covers only the test function body, so queries in fixture
   setup and teardown are not counted.
3. Each statement is stored with the first stack frame outside SQLAlchemy,
   pytest, the standard library, `site-packages` and the plugin itself. For
   async sessions the search continues in the parent greenlets, where the
   awaiting code runs.
4. With the index check on, an `after_cursor_execute` listener runs, on the
   same connection and inside a savepoint, `SET LOCAL enable_seqscan = off`
   (through `set_config`) and
   `EXPLAIN (GENERIC_PLAN, VERBOSE, FORMAT JSON)` for each new `SELECT`, with
   the driver's placeholders rewritten to `$1`, `$2`, ..., then rolls back to
   the savepoint. The SQL goes through a raw DB-API cursor, so SQLAlchemy
   events, budgets and your own listeners do not see it.
5. After the test, the recorded events are checked against the budget, the
   two N+1 rules and the scans found by the index check. A bug inside the
   plugin disables it with one warning; it never fails your tests.

## Limitations

- SQLAlchemy only. Raw DB-API connections and other ORMs are not seen.
- Everything in `site-packages` counts as library code. If your application
  is installed non-editable (for example `pip install .` in a Docker CI
  image), its frames are skipped too, and call sites point to the test file
  instead of the application line.
- One measurement is active per process at a time, so tests in one process
  must run sequentially. Under pytest-xdist each worker measures its own
  tests, so detection and budgets work. The report needs a single process,
  so under pytest-xdist it prints
  `queryguard report: not available with pytest-xdist`.
- Tests that use `pytester` to run pytest in-process are also measured by the
  outer test, which sees the inner run's queries. Mark such tests with
  `allow_nplusone` or use `pytester.runpytest_subprocess()`.
- unittest `setUp` and `tearDown` run inside the test call, so their queries
  are counted, unlike pytest fixtures.
- A non-strict `xfail` test that has an N+1 is reported as xfailed rather
  than XPASS, because the N+1 failure looks like the expected failure.
- `INSERT`, `UPDATE` and `DELETE` are never reported as N+1. At cursor level
  a unit-of-work flush cannot be told apart from an application write, so
  all writes are left out. Use a budget to catch write loops.
- The index check needs PostgreSQL 16 or newer and a sync engine. Async
  engines (asyncpg, psycopg's async mode) prepare every statement, and
  PostgreSQL rejects `EXPLAIN (GENERIC_PLAN)` with `$1` placeholders sent
  that way, so their statements are skipped with a notice. The test suite
  uses pg8000. psycopg2 and psycopg 3 were checked by hand during review
  and give the same findings for plain statements, `%` in SQL, expanding
  `IN`, autocommit and `stream_results`; they are LGPL-licensed, so this
  project does not depend on them or run them in CI.
- The index check looks only at `SELECT` and `WITH` statements and judges
  plans, not data: it says that no index can serve a filter, not that an
  existing index is a good one. A missing index that only a join would use
  (a join without a `WHERE` on that table) is not reported, because the
  plan for it depends on statistics.
- A partitioned table is reported under the partition's name, and
  `queryguard_index_ignore_tables` matches partition names, not the parent
  table's.
- The index check's session summary is printed by the process that runs the
  tests, so under pytest-xdist it is not shown. Findings in tests are
  reported as usual.
- On pytest 9.0 and 9.1, a wrongly typed value in the native `[tool.pytest]`
  table for one of pytest's own options (for example
  `console_output_style = 3`) stops pytest with an internal error, with or
  without this plugin. Wrong types for this plugin's options are reported
  as usage errors.

## Comparison with nplusone

[nplusone](https://github.com/jmcarp/nplusone) solved the same problem
earlier and also covers Django and Peewee. Its last release, 1.0.0, came
out in 2018, and it does not support SQLAlchemy 2. pytest-queryguard targets
SQLAlchemy 2 only, including async sessions, and adds query budgets and a
fix hint in the failure message. It does not detect unused eager loads,
which nplusone does.

## Roadmap

- 0.2 (unreleased): missing index detection on PostgreSQL 16 and newer, see
  [Missing indexes](#missing-indexes-postgresql-16-and-newer).
- Later: the index check for async engines, Django ORM support.

## Development

The project uses [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run coverage run -m pytest
uv run coverage report
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

The PostgreSQL tests run when `QUERYGUARD_PG_URL` points to a server, and
are skipped otherwise:

```bash
docker run -d --rm --name qg-postgres -p 55432:5432 -e POSTGRES_PASSWORD=pg postgres:17
QUERYGUARD_PG_URL=postgresql+pg8000://postgres:pg@localhost:55432/postgres uv run pytest
docker stop qg-postgres
```

Coverage is measured with `coverage run` rather than `pytest --cov`, because
`--cov` starts after pytest has imported the plugin and misses its
import-time lines.

## License

MIT, see the LICENSE file.
