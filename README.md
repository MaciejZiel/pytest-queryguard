# pytest-queryguard

[![CI](https://github.com/MaciejZiel/pytest-queryguard/actions/workflows/ci.yml/badge.svg)](https://github.com/MaciejZiel/pytest-queryguard/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/pytest-queryguard)](https://pypi.org/project/pytest-queryguard/)
[![Python](https://img.shields.io/pypi/pyversions/pytest-queryguard)](https://pypi.org/project/pytest-queryguard/)
[![License](https://img.shields.io/pypi/l/pytest-queryguard)](https://github.com/MaciejZiel/pytest-queryguard/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-maciejziel.github.io-blue)](https://maciejziel.github.io/pytest-queryguard/)

A pytest plugin that fails tests with N+1 queries and enforces query budgets
for SQLAlchemy 2, in sync, async and FastAPI code. On PostgreSQL 16 and newer
and on SQLite it can also fail tests whose queries have no index to use.

Documentation: <https://maciejziel.github.io/pytest-queryguard/>

ORM code that looks correct can send one query per row instead of one query
per page. The tests pass, because a test database holds a handful of rows; in
production the same endpoint sends hundreds of queries. pytest-queryguard
watches the SQL each test sends, and when it sees the pattern it fails the
test and points to the line that caused it, even if the test database has
only a few rows.

## 30-second example

This code passes its tests without the plugin (`app/orders.py`):

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Order


def list_orders(session: Session) -> list[dict[str, object]]:
    rows = []
    for order in session.scalars(select(Order)).all():
        rows.append({"id": order.id, "customer": order.customer.name})
    return rows
```

```python
def test_list_orders(session):
    assert len(list_orders(session)) == 50
```

Install the plugin and run pytest. There is nothing to import or configure:

```bash
pip install pytest-queryguard
```

```
queryguard: N+1 detected

  Order.customer was lazy-loaded 50 times
  first at app/orders.py:10 in list_orders
  sql: SELECT customer.id, customer.name FROM customer WHERE customer.id = ?
  fix: load it with the parent query, e.g.
       select(Order).options(joinedload(Order.customer))
```

With `joinedload(Order.customer)` the same test passes. The whole project
(models, fixture, the fixed code, a loop of `session.get()` and a query budget)
is in [examples/sync-n-plus-one](https://github.com/MaciejZiel/pytest-queryguard/tree/main/examples/sync-n-plus-one), and a test runs it
and checks this output. Python 3.10 or newer, pytest 8 or newer and SQLAlchemy
2.0 or newer are needed. For async code on SQLAlchemy 2.1 and newer, also
install `sqlalchemy[asyncio]`.

## What it finds

Every test is checked automatically. A test fails when, inside the test
function:

- **relationship-load**: one relationship (`Order.customer`) is lazy-loaded
  for 2 or more different parent objects. The hint is `joinedload` for a
  many-to-one relationship and `selectinload` for a collection;
- **repeated-statement**: one `SELECT`, with its parameters and literals
  removed, runs 5 or more times. Core and `text()` statements count, not only
  ORM ones;
- **column-load**: 5 or more instances of one class each go back to the
  database for a deferred column (hint: `undefer`) or for attributes expired by
  `commit()` (hint: `expire_on_commit=False`);
- **write-loop** (opt-in): the same `INSERT`, `UPDATE` or `DELETE`, one row per
  statement, runs many times from one source line;
- **unused-eager-load** (experimental, off by default): a relationship is
  eager-loaded and never read;
- **missing-index** (opt-in): see below.

All thresholds are options. A budget fails a test that runs too many
statements, and `max_db_time` one that spends too long in the database:

```python
@pytest.mark.max_queries(3)
def test_order_page(client):
    client.get("/orders/1")
```

The failure lists every statement with the line that issued it. FastAPI's
`TestClient` runs the application in another thread; queries from any thread
are counted. [How detection works](https://maciejziel.github.io/pytest-queryguard/how-detection-works.html) explains each
rule and why the thresholds are what they are.

## Missing indexes

A test database holds a few rows, so a query that reads the whole table is as
fast in CI as one that uses an index. With the index check on, every `SELECT`
in a test body is planned again with sequential scans switched off (PostgreSQL
16 and newer), explained with `EXPLAIN QUERY PLAN` (SQLite), or explained with `EXPLAIN` (MySQL and MariaDB). If the plan
still reads a whole table to apply a filter, no index can serve it, however
small the table:

```bash
pytest --queryguard-index-check
```

```
queryguard: missing index

  orders is read in full: Seq Scan with filter (orders.customer_id = $1)
  first at app/orders.py:11 in orders_of
  sql: SELECT orders.id, orders.customer_id FROM orders WHERE orders.customer_id = %s::INTEGER
  fix: CREATE INDEX ON orders (customer_id)
  alembic: op.create_index("ix_orders_customer_id", "orders", ["customer_id"])

  a sequential scan is fine on a small lookup table:
  list it in queryguard_index_ignore_tables
```

The index advisor suggests the `CREATE INDEX` and the Alembic call; with the
hypopg extension in the test database it also says whether the planner would
use the index and what the estimated cost would be before and after. The
check is off by default. See [PostgreSQL](https://maciejziel.github.io/pytest-queryguard/index-check.html),
[SQLite](https://maciejziel.github.io/pytest-queryguard/index-check-sqlite.html), [MySQL and MariaDB](https://maciejziel.github.io/pytest-queryguard/index-check-mysql.html) (tested on live servers by hand, not in CI) and the
[index advisor](https://maciejziel.github.io/pytest-queryguard/index-advisor.html); runnable projects are in
[examples/postgres-missing-index](https://github.com/MaciejZiel/pytest-queryguard/tree/main/examples/postgres-missing-index) and
[examples/sqlite-index-check](https://github.com/MaciejZiel/pytest-queryguard/tree/main/examples/sqlite-index-check).

## Adopting it on an existing project

- Record today's findings once, and only new ones fail:
  `pytest --queryguard-baseline=queryguard-baseline.json --queryguard-update-baseline`.
  See [Baseline](https://maciejziel.github.io/pytest-queryguard/baseline.html).
- `pytest --queryguard-sarif=queryguard.sarif` writes the findings as SARIF
  for GitHub code scanning, and the action in [action](https://github.com/MaciejZiel/pytest-queryguard/tree/main/action) runs pytest and
  uploads it. See [CI and GitHub Action](https://maciejziel.github.io/pytest-queryguard/ci.html) and
  [SARIF](https://maciejziel.github.io/pytest-queryguard/sarif.html).
- Leave out single findings with `@pytest.mark.allow_nplusone("Order.customer")`
  or `queryguard_ignore`; make kinds warn with `queryguard_fail_on` or
  `queryguard_mode = warn`. See [Ignoring findings](https://maciejziel.github.io/pytest-queryguard/ignoring.html).
- Read the statements of a block in a test with `record_queries()`. See
  [Reading the queries](https://maciejziel.github.io/pytest-queryguard/python-api.html).

```toml
[tool.pytest.ini_options]
queryguard_mode = "warn"                  # fail (default), warn or off
queryguard_repeat_threshold = 10
queryguard_baseline = "queryguard-baseline.json"
queryguard_index_check = true
queryguard_index_ignore_tables = ["countries", "lookup_*"]
```

Every option, flag and marker is in the
[configuration reference](https://maciejziel.github.io/pytest-queryguard/configuration.html).

## Documentation

| Page | What is in it |
|---|---|
| [Getting started](https://maciejziel.github.io/pytest-queryguard/getting-started.html) | Install, a first test, async and FastAPI |
| [How detection works](https://maciejziel.github.io/pytest-queryguard/how-detection-works.html) | The rules, the thresholds and why |
| [Configuration reference](https://maciejziel.github.io/pytest-queryguard/configuration.html) | Ini options, flags, markers |
| [Budgets and timing](https://maciejziel.github.io/pytest-queryguard/budgets.html) | `max_queries`, `max_db_time` |
| [Baseline](https://maciejziel.github.io/pytest-queryguard/baseline.html), [CI and Action](https://maciejziel.github.io/pytest-queryguard/ci.html), [SARIF](https://maciejziel.github.io/pytest-queryguard/sarif.html) | Adoption and code scanning |
| [Recipes](https://maciejziel.github.io/pytest-queryguard/recipes.html) | FastAPI, Flask-SQLAlchemy, SQLModel, Litestar, pytest-asyncio, budgets, legacy projects, CI |
| [Index check](https://maciejziel.github.io/pytest-queryguard/index-check.html), [SQLite](https://maciejziel.github.io/pytest-queryguard/index-check-sqlite.html), [advisor](https://maciejziel.github.io/pytest-queryguard/index-advisor.html) | Missing indexes |
| [Unused eager loads](https://maciejziel.github.io/pytest-queryguard/unused-eager-load.html) | The experimental check |
| [FAQ](https://maciejziel.github.io/pytest-queryguard/faq.html), [Limitations](https://maciejziel.github.io/pytest-queryguard/limitations.html) | `raiseload`, false positives, xdist, drivers |
| [Comparison](https://maciejziel.github.io/pytest-queryguard/comparison.html), [Migrating from nplusone](https://maciejziel.github.io/pytest-queryguard/migrating-from-nplusone.html) | Other tools |
| [Stability and versioning](https://maciejziel.github.io/pytest-queryguard/stability.html) | What is public API, deprecations |
| [Changelog](https://github.com/MaciejZiel/pytest-queryguard/blob/main/CHANGELOG.md), [examples](https://github.com/MaciejZiel/pytest-queryguard/tree/main/examples) | |

The pages are Markdown in [docs](https://github.com/MaciejZiel/pytest-queryguard/tree/main/docs); `scripts/build_docs.py` builds them
into a static site (see Development).

## How it compares

Other tools find N+1 queries too: [queryspy](https://github.com/sqla-native/queryspy),
[pytest-querycount](https://github.com/ldavidsm/pytest-querycount),
[nplusone](https://github.com/jmcarp/nplusone) (last release 2018),
[django-zeal](https://github.com/taobojlen/django-zeal) for Django and others.
The [comparison page](https://maciejziel.github.io/pytest-queryguard/comparison.html) has a feature matrix (dated, with what
was run and what was only read) and the results of running 45 scenarios against
queryspy 0.5.0 and pytest-querycount 0.4.0. In short, as of 2026-10-12, in
my runs: pytest-queryguard fails on N+1 with no configuration (the other two
need a flag or a marker), reports loops of Core and `text()` statements
(queryspy did not) and lists the statements in a budget failure (queryspy
printed only the counts). queryspy and pytest-querycount also report runs of
2 to 4 identical statements, which pytest-queryguard does only when you set
`queryguard_small_repeat_threshold`. pytest-querycount has a PostgreSQL
missing-index check of its own, queryspy has middleware and a request panel for
running apps, which pytest-queryguard does not, and django-zeal covers Django.
Moving from nplusone: [migration guide](https://maciejziel.github.io/pytest-queryguard/migrating-from-nplusone.html).

## Overhead

On in-memory SQLite, the measurement adds about 23 us (Core, +37 %) or 54 us
(ORM, +41 %) per statement. Those percentages are an artifact of a very cheap
database: a real round trip takes milliseconds. Details and the cost of the
opt-in checks are in the [FAQ](https://maciejziel.github.io/pytest-queryguard/faq.html#does-it-slow-my-tests-down).

## Limitations

- SQLAlchemy only; raw DB-API connections (`sqlite3` and the like) and other
  ORMs (Django ORM, Peewee) are not seen, and nothing says so.
- Only the test function body is measured, not fixtures. One measurement is
  active per process; under pytest-xdist each worker measures its own tests.
- The PostgreSQL index check needs PostgreSQL 16 or newer and a sync engine or
  asyncpg; the SQLite, MySQL and MariaDB checks judge less precisely. It says that no index can
  serve a filter, not that an existing index is a good one.
- Writes never count towards the repeated-statement rule; the opt-in
  `write-loop` kind and budgets cover them.

The full list is on the [Limitations](https://maciejziel.github.io/pytest-queryguard/limitations.html) page.

## Development

The project uses [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run coverage run -m pytest
uv run coverage report
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run --group docs python scripts/build_docs.py --strict   # the docs site, into site/
```

The PostgreSQL tests run when `QUERYGUARD_PG_URL` points to a server, and are
skipped otherwise:

```bash
docker run -d --rm --name qg-postgres -p 55432:5432 -e POSTGRES_PASSWORD=pg postgres:17
QUERYGUARD_PG_URL=postgresql+pg8000://postgres:pg@localhost:55432/postgres uv run pytest
docker stop qg-postgres
```

Coverage is measured with `coverage run` rather than `pytest --cov`, because
`--cov` starts after pytest has imported the plugin and misses its import-time
lines. Third-party licenses are listed in
[3rdparty_licenses.md](https://github.com/MaciejZiel/pytest-queryguard/blob/main/3rdparty_licenses.md).

## License

MIT, see the LICENSE file.
