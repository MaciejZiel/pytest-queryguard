# pytest-queryguard

[![CI](https://github.com/MaciejZiel/pytest-queryguard/actions/workflows/ci.yml/badge.svg)](https://github.com/MaciejZiel/pytest-queryguard/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/pytest-queryguard)](https://pypi.org/project/pytest-queryguard/)
[![Python](https://img.shields.io/pypi/pyversions/pytest-queryguard)](https://pypi.org/project/pytest-queryguard/)
[![License](https://img.shields.io/pypi/l/pytest-queryguard)](https://github.com/MaciejZiel/pytest-queryguard/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-maciejziel.github.io-blue)](https://maciejziel.github.io/pytest-queryguard/)

A pytest plugin that fails a test when the SQLAlchemy 2 code it runs sends one
query per row (N+1), and enforces query budgets. Sync, async and FastAPI code;
nothing to import or configure. A test database holds a handful of rows, so
such code passes its tests and sends hundreds of queries in production. This
test passes without the plugin:

```python
def test_list_orders(session):
    assert len(list_orders(session)) == 50
```

With the plugin installed it fails, and points to the line to fix:

```
queryguard: N+1 detected

  Order.customer was lazy-loaded 50 times
  first at app/orders.py:10 in list_orders
    called from tests/test_orders.py:7 in test_list_orders
  parents loaded at app/orders.py:9 in list_orders
  kind: relationship-load  label: Order.customer
  docs: https://maciejziel.github.io/pytest-queryguard/how-detection-works.html#relationship-load
  sql: SELECT customer.id, customer.name FROM customer WHERE customer.id = ?
  fix: load it with the parent query, e.g.
       select(Order).options(joinedload(Order.customer))
```

Python 3.10+, pytest 8+, SQLAlchemy 2.0.13+; tested on SQLite, PostgreSQL 14 to 18, MySQL 8.4 and MariaDB 11.8. Django ORM support is experimental: [Django](https://maciejziel.github.io/pytest-queryguard/django.html). Django projects that run `manage.py test` instead of pytest can use its test runner: [Without pytest](https://maciejziel.github.io/pytest-queryguard/django.html#without-pytest-managepy-test).

## What it catches

| Check | Default | Fails a test when |
|---|---|---|
| N+1 on a relationship | on | one relationship is lazy-loaded for 2 or more parent objects |
| Repeated statement | on | one `SELECT` runs 5 or more times: ORM, Core or `text()` |
| Column reloads | on | 5 or more objects reload a deferred or expired column one by one |
| Cartesian joins | on, warns | one query `joinedload`s two collections side by side, so its rows grow as their product (`fail` makes it fail) |
| Query and time budgets | per test | `@pytest.mark.max_queries(n)` or `max_db_time(seconds)` is exceeded |
| Row-by-row writes | opt-in | one `INSERT`, `UPDATE` or `DELETE` runs once per row from one line |
| Missing index, with the `CREATE INDEX` | opt-in | no index can serve a filter (PostgreSQL 16+, SQLite, MySQL, MariaDB) |
| Unused eager loads | experimental, off | a relationship is eager-loaded and never read |
| Unused columns | experimental, off | a query loads a wide row and the test reads few of its columns |

Every test body is checked. The checks look at the application's statements:
an N+1 in the test's own code (the test function, `conftest.py`, factories,
helpers under `tests/`) never runs in production, so it is counted at the end
of the run instead of failing the test (see
[Test code](https://maciejziel.github.io/pytest-queryguard/how-detection-works.html#test-code)).
[How detection works](https://maciejziel.github.io/pytest-queryguard/how-detection-works.html)
explains each rule and its threshold.

## How it compares

The [comparison page](https://maciejziel.github.io/pytest-queryguard/comparison.html)
has a dated feature matrix and the method; the numbers below are from it.
On 2026-11-10 a [head-to-head run in GitHub Actions](https://maciejziel.github.io/pytest-queryguard/comparison.html#head-to-head-in-github-actions)
ran 278 scenarios of the [accuracy corpus](https://maciejziel.github.io/pytest-queryguard/accuracy.html)
(135 with an N+1 or a similar problem, 143 without) under each pytest tool
that supports SQLAlchemy 2, each set up the way its own documentation says to
fail a test:

| Tool | Scenarios with a problem flagged | Scenarios without one flagged | 300-test suite, added time (median) |
|---|---|---|---|
| pytest-queryguard 1.0.0rc1, defaults | 117 of 135 | 2 of 143 | +13.1% |
| queryspy 0.6.0, `--queryspy-strict` | 110 of 135 | 34 of 143 | +25.7% |
| pytest-querycount 0.4.0, a marker on every test | 115 of 135 | 35 of 143 | +15.6% |
| n-plus-one-sniffer `d4790f8`, a marker on every test | 115 of 135 | 30 of 143 | +22.9% |
| nplusone 1.0.0 | does not import on SQLAlchemy 2.1; 1 of 135 on 2.0 | | |

The corpus was written by the author of pytest-queryguard, and its scenarios
without a problem include the shapes this plugin was taught to leave alone,
so read the table with that bias. What it does show: pytest-queryguard fails
tests with no marker, option or fixture; it reports loops of Core `select()`
and `text()` statements as well as lazy loads; and it rarely flags fixed
code. The [accuracy page](https://maciejziel.github.io/pytest-queryguard/accuracy.html#results-default-configuration)
has the corpus measured again with the current code and its defaults: 117 of
the 121 scenarios with a problem the default rules can report are flagged,
and 2 of the 157 without one. Where the others are ahead (Django support, runs of 2 to 4 identical
statements by default, exact SQL snapshots) is on the
[same page](https://maciejziel.github.io/pytest-queryguard/comparison.html#where-the-others-are-ahead).

## Quick start

In a project that has pytest tests for its SQLAlchemy code:

```bash
pip install pytest-queryguard
pytest
```

There is nothing else to set up. To see a failure first, run the example
project, which has an N+1, a `session.get()` loop and a budget:

```bash
git clone --depth 1 https://github.com/MaciejZiel/pytest-queryguard
cd pytest-queryguard/examples/sync-n-plus-one
python -m venv .venv && . .venv/bin/activate
pip install pytest-queryguard
pytest tests/test_orders.py
```

Three tests fail; `tests/test_orders_fixed.py` has the same tests against the
fixed code and passes. The code behind the failure above is `app/orders.py`:

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

With `joinedload(Order.customer)` in the query the same test passes. When you
try it on code of your own, put the loop in a module of the application and
call it from the test: a loop written in the test function is test code, and
the run then ends with a `queryguard: test code` section that counts it and
says how to see it (`pytest -o queryguard_test_code=warn`). For async code on
SQLAlchemy 2.1 and newer, also install `sqlalchemy[asyncio]`.
[Getting started](https://maciejziel.github.io/pytest-queryguard/getting-started.html)
walks through the example, async code and FastAPI.

## Coming from nplusone?

nplusone has had no release since 1.0.0 (2018-05-21). It patches
`Query.__iter__`, so on SQLAlchemy 2.0 it misses the parents loaded by a
`select()`-style query (`session.scalars(select(Order))`), and on SQLAlchemy
2.1 `import nplusone.ext.sqlalchemy` fails. The
[migration guide](https://maciejziel.github.io/pytest-queryguard/migrating-from-nplusone.html)
has a find-and-replace table: delete the profiler fixture and the imports,
`NPLUSONE_WHITELIST` becomes `queryguard_ignore`, `NPLUSONE_RAISE = False`
becomes `queryguard_mode = "warn"`, `signals.ignore()` becomes
`pytest_queryguard.ignore()`, and a before and after of one project.

## Coming from queryspy?

queryspy reports N+1s but fails a test only with `--queryspy-strict` or
`queryspy_fail_on`; pytest-queryguard fails by default, so start with
`queryguard_mode = "warn"` if you ran queryspy without them. Its markers and
helpers have equivalents (`@pytest.mark.queryspy(max_queries=3)` is
`@pytest.mark.max_queries(3)`, `assert_max_queries()` is `max_queries()`,
`no_n_plus_one()` is `record_queries()` with `assert_no_nplusone()`), and two
defaults differ: repeats and column reloads need 5 runs here, not 2, and
loops in test code are counted, not reported. The
[migration guide](https://maciejziel.github.io/pytest-queryguard/migrating-from-queryspy.html)
has the full find-and-replace table.

## Missing indexes

A test database holds a few rows, so a query that reads the whole table is as
fast in CI as one that uses an index. `pytest --queryguard-index-check` plans
every `SELECT` of a test again with sequential scans switched off (PostgreSQL
16 and newer), or reads `EXPLAIN QUERY PLAN` (SQLite) or `EXPLAIN` (MySQL and
MariaDB). If the plan still reads a whole table to apply a filter, no index
can serve it:

```
queryguard: missing index

  orders is read in full: Seq Scan with filter (orders.customer_id = $1)
  first at app/orders.py:11 in orders_of
  kind: missing-index  label: orders
  docs: https://maciejziel.github.io/pytest-queryguard/how-detection-works.html#missing-index
  sql: SELECT orders.id, orders.customer_id FROM orders WHERE orders.customer_id = %s::INTEGER
  fix: CREATE INDEX ON orders (customer_id)
  alembic: op.create_index("ix_orders_customer_id", "orders", ["customer_id"])

  a sequential scan is fine on a small lookup table:
  list it in queryguard_index_ignore_tables
```

The check is off by default. See [PostgreSQL](https://maciejziel.github.io/pytest-queryguard/index-check.html),
[SQLite](https://maciejziel.github.io/pytest-queryguard/index-check-sqlite.html),
[MySQL and MariaDB](https://maciejziel.github.io/pytest-queryguard/index-check-mysql.html)
and the [index advisor](https://maciejziel.github.io/pytest-queryguard/index-advisor.html),
which can verify a suggested index with hypopg.

## Adopting it on an existing project

- Record today's findings once, and only new ones fail:
  `pytest --queryguard-baseline=queryguard-baseline.json --queryguard-update-baseline`.
  See [Baseline](https://maciejziel.github.io/pytest-queryguard/baseline.html).
- `pytest --queryguard-sarif=queryguard.sarif` writes the findings as SARIF
  for GitHub code scanning, and the [GitHub Action](https://github.com/MaciejZiel/pytest-queryguard/tree/main/action)
  runs pytest and uploads it. See [CI and GitHub Action](https://maciejziel.github.io/pytest-queryguard/ci.html).
- Leave out single findings with `@pytest.mark.queryguard_ignore("Order.customer")`
  or the `queryguard_ignore` option; make kinds warn with `queryguard_fail_on`
  or `queryguard_mode = warn`. See [Ignoring findings](https://maciejziel.github.io/pytest-queryguard/ignoring.html).
- Read the statements of a block in a test with `record_queries()`. See
  [Reading the queries](https://maciejziel.github.io/pytest-queryguard/python-api.html).
- The same rules run per request in a FastAPI, Starlette or Flask app during
  development: [in-application mode](https://maciejziel.github.io/pytest-queryguard/in-app.html).

```toml
[tool.pytest.ini_options]
queryguard_mode = "warn"                  # fail (default), warn or off
queryguard_repeat_threshold = 10
# the file must exist: create it first with --queryguard-update-baseline
queryguard_baseline = "queryguard-baseline.json"
queryguard_index_check = true
queryguard_index_ignore_tables = ["countries", "lookup_*"]
```

Every option, flag and marker is in the
[configuration reference](https://maciejziel.github.io/pytest-queryguard/configuration.html).

## Overhead

On the 300-test suite of the comparison (about 5 ms a test), pytest-queryguard
1.0.0rc1 added 13 to 15 % to the run time on a GitHub runner. Per statement, on in-memory SQLite, the current code
adds about 16 us to a Core statement and 34 to 36 us to an ORM one, which is
a large share of a statement that costs almost nothing; against a database
on disk or over the network the share is smaller. Method and numbers: [Performance](https://maciejziel.github.io/pytest-queryguard/performance.html)
and the [FAQ](https://maciejziel.github.io/pytest-queryguard/faq.html#does-it-slow-my-tests-down).

## Limitations

- SQLAlchemy, and Django experimentally; raw DB-API connections (`sqlite3`
  and the like) and other ORMs (Peewee) are not seen, and nothing says so.
- Only the test function body is measured, not fixtures. One measurement is
  active per process; under pytest-xdist each worker measures its own tests.
- Test code is told from the application by file: application code kept in
  a test module or under `tests/` is taken for test code.
- The PostgreSQL index check needs PostgreSQL 16 or newer and a sync engine or
  asyncpg; the SQLite, MySQL and MariaDB checks judge less precisely. It says
  that no index can serve a filter, not that an existing index is a good one.
- A finding says that a pattern happened, not that production is slow.

The full list is on the [Limitations](https://maciejziel.github.io/pytest-queryguard/limitations.html) page.

## Stability

1.0 is stable: the public API (options, markers, finding kinds, files, the action) breaks only in a
major release, deprecations last two minor releases, and fixes go into the latest 1.x. Experimental
features (static check, MCP server, Django) are exempt. See [Stability](https://maciejziel.github.io/pytest-queryguard/stability.html).

## Documentation

<https://maciejziel.github.io/pytest-queryguard/>, starting with
[Getting started](https://maciejziel.github.io/pytest-queryguard/getting-started.html) and
[A test failed: now what](https://maciejziel.github.io/pytest-queryguard/when-a-test-fails.html).

| Page | What is in it |
|---|---|
| [How detection works](https://maciejziel.github.io/pytest-queryguard/how-detection-works.html) | The rules, the thresholds and why |
| [Configuration reference](https://maciejziel.github.io/pytest-queryguard/configuration.html) | Ini options, flags, markers |
| [Budgets and timing](https://maciejziel.github.io/pytest-queryguard/budgets.html) | `max_queries`, `max_db_time`, a budget file |
| [Baseline](https://maciejziel.github.io/pytest-queryguard/baseline.html), [CI and Action](https://maciejziel.github.io/pytest-queryguard/ci.html), [SARIF](https://maciejziel.github.io/pytest-queryguard/sarif.html) | Adoption and code scanning |
| [Recipes](https://maciejziel.github.io/pytest-queryguard/recipes.html) | FastAPI, Flask-SQLAlchemy, SQLModel, Litestar, pytest-asyncio, Factory Boy, query counts that must not grow, Sentry N+1 issues, legacy projects, CI |
| [In-application mode](https://maciejziel.github.io/pytest-queryguard/in-app.html) | The same rules per request in a running app; a development panel |
| [FAQ](https://maciejziel.github.io/pytest-queryguard/faq.html), [Limitations](https://maciejziel.github.io/pytest-queryguard/limitations.html) | `raiseload`, false positives, xdist, drivers |
| [Comparison](https://maciejziel.github.io/pytest-queryguard/comparison.html), [accuracy](https://maciejziel.github.io/pytest-queryguard/accuracy.html) | Other tools, precision and recall |
| [Stability and versioning](https://maciejziel.github.io/pytest-queryguard/stability.html) | What is public API, deprecations |
| [Changelog](https://github.com/MaciejZiel/pytest-queryguard/blob/main/CHANGELOG.md), [examples](https://github.com/MaciejZiel/pytest-queryguard/tree/main/examples) | |

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

The PostgreSQL tests run when `QUERYGUARD_PG_URL` points to a server, and are skipped otherwise:

```bash
docker run -d --rm --name qg-postgres -p 55432:5432 -e POSTGRES_PASSWORD=pg postgres:17
QUERYGUARD_PG_URL=postgresql+pg8000://postgres:pg@localhost:55432/postgres uv run pytest
docker stop qg-postgres
```

Coverage is measured with `coverage run` rather than `pytest --cov`, because
`--cov` starts after pytest has imported the plugin and misses its import-time
lines. Third-party licenses are listed in
[3rdparty_licenses.md](https://github.com/MaciejZiel/pytest-queryguard/blob/main/3rdparty_licenses.md).

Thanks to [@Muguett-DBY](https://github.com/Muguett-DBY) for fixes and tests: [#202](https://github.com/MaciejZiel/pytest-queryguard/pull/202), [#203](https://github.com/MaciejZiel/pytest-queryguard/pull/203), [#210](https://github.com/MaciejZiel/pytest-queryguard/pull/210), [#211](https://github.com/MaciejZiel/pytest-queryguard/pull/211), [#287](https://github.com/MaciejZiel/pytest-queryguard/pull/287), [#293](https://github.com/MaciejZiel/pytest-queryguard/pull/293).

## License

MIT, see the [LICENSE](https://github.com/MaciejZiel/pytest-queryguard/blob/main/LICENSE) file.
