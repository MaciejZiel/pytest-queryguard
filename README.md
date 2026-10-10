# pytest-queryguard

[![CI](https://github.com/MaciejZiel/pytest-queryguard/actions/workflows/ci.yml/badge.svg)](https://github.com/MaciejZiel/pytest-queryguard/actions/workflows/ci.yml)

A pytest plugin that fails tests with N+1 queries and enforces query budgets
for SQLAlchemy 2, in sync, async and FastAPI code.

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
============================== 3 failed in 0.06s ===============================
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
`queryguard_mode` is set to.

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

## Configuration

| Option | Values | Default | Meaning |
|---|---|---|---|
| `queryguard_mode` | `fail`, `warn`, `off` | `fail` | `fail` fails a test with N+1. `warn` reports it as a `QueryGuardWarning` and lets the test pass. `off` turns detection off; tests without a budget are not measured. |
| `queryguard_relationship_threshold` | integer ≥ 2 | `2` | Number of different parent objects for which one relationship must be lazy-loaded to count as N+1. |
| `queryguard_repeat_threshold` | integer ≥ 2 | `5` | Number of times one normalized `SELECT` must run to count as N+1. |

Any invalid value stops pytest with a usage error.

In `pyproject.toml`:

```toml
[tool.pytest.ini_options]
queryguard_mode = "warn"
queryguard_relationship_threshold = 3
queryguard_repeat_threshold = 10
```

With pytest 9 you can use the native TOML table instead:

```toml
[tool.pytest]
queryguard_mode = "warn"
queryguard_relationship_threshold = 3
queryguard_repeat_threshold = 10
```

`pytest.ini` and `setup.cfg` work as usual.

Command-line options:

| Option | Meaning |
|---|---|
| `--queryguard-mode=fail\|warn\|off` | Overrides `queryguard_mode` for this run. |
| `--queryguard-report` | Prints the 10 tests with the most queries at the end of the session. |

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
4. After the test, the recorded events are checked against the budget and
   the two N+1 rules. A bug inside the plugin disables it with one warning;
   it never fails your tests.

## Limitations

- SQLAlchemy only. Raw DB-API connections and other ORMs are not seen.
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

## Comparison with nplusone

[nplusone](https://github.com/jmcarp/nplusone) solved the same problem
earlier and also covers Django and Peewee. Its last release, 1.0.0, came
out in 2018, and it does not support SQLAlchemy 2. pytest-queryguard targets
SQLAlchemy 2 only, including async sessions, and adds query budgets and a
fix hint in the failure message. It does not detect unused eager loads,
which nplusone does.

## Roadmap

- 0.2: missing index detection on PostgreSQL 16 and newer, by running each
  statement through `EXPLAIN (GENERIC_PLAN)` with `enable_seqscan=off`.
- Later: Django ORM support.

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

Coverage is measured with `coverage run` rather than `pytest --cov`, because
`--cov` starts after pytest has imported the plugin and misses its
import-time lines.

## License

MIT, see the LICENSE file.
