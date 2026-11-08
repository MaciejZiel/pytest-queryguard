# Migrating from nplusone

[nplusone](https://github.com/jmcarp/nplusone) detects N+1 queries for Django,
SQLAlchemy and Peewee. Its last release, 1.0.0, is from 2018-05-21. This page
is for projects that used it with SQLAlchemy and now move to SQLAlchemy 2 and
pytest-queryguard. If you use nplusone with Django, see
[Django](django.md): pytest-queryguard's Django support is experimental, and
[Comparison](comparison.md) lists other tools that cover Django.

## If you only want nplusone to keep working

[nplus1](https://pypi.org/project/nplus1/) is a rewrite of nplusone that
installs under the same import name, `nplusone`. Replace `nplusone` with
`nplus1` in your requirements and the imports, the `Profiler`, the
`NPLUSONE_*` settings and the whitelist stay as they are. That is the
smallest change, and for Django, Flask or Peewee it may be all you need.

On SQLAlchemy it does not get you to 2.1 or to 2.0-style queries. I ran
nplus1 1.1.5 on 2026-11-08 with the loop from [the table
below](#why-a-migration-is-needed): on SQLAlchemy 2.0.54 it reports the loop
over `session.query(Order)` and nothing for `session.scalars(select(Order))`;
on 2.1.1 `import nplusone.ext.sqlalchemy` fails with the same `AttributeError`
as nplusone 1.0.0.

What moving to pytest-queryguard adds, besides SQLAlchemy 2.0 and 2.1 with
`select()`, async sessions and Core:

- every test is checked with no fixture, and the failure names the
  application line, the test line that called it and the fix;
- repeated statements (`session.get()` in a loop, Core and `text()`), column
  loads and, opt-in, write loops, not only lazy relationships;
- query budgets (`max_queries`, a budget file) and a missing-index check;
- a [baseline](baseline.md) to adopt it on a suite with existing findings,
  and SARIF and JSON output for CI.

## Why a migration is needed

nplusone patches `Query.__iter__` and SQLAlchemy's lazy loader. A 2.0-style
query (`session.scalars(select(Order))`, `session.execute(select(...))`) does
not go through `Query.__iter__`, so on SQLAlchemy 2.0 nplusone does not see
the parents it loads and reports nothing; on 2.1 it does not import.

I installed nplusone 1.0.0 next to several SQLAlchemy versions (Python 3.13
for the first three rows, Python 3.9 for the last) and ran the same N+1 loop:

| SQLAlchemy | What happened |
|---|---|
| 2.1.4 | `import nplusone.ext.sqlalchemy` fails with `AttributeError: module 'sqlalchemy.orm.strategies' has no attribute 'LazyLoader'` |
| 2.0.54 | Imports. A loop over `session.query(Order)` fails with `AttributeError: 'Query' object has no attribute '_offset'`. A loop over `session.scalars(select(Order))` runs and reports nothing |
| 1.4.54 | The same two outcomes as 2.0.54 |
| 1.3.24 | Works: `NPlusOneError: Potential n+1 query detected on Order.customer` |

The [head-to-head run](comparison.md#head-to-head-in-github-actions) of
2026-11-07 gives the same picture on a larger set: on the 252 scenarios of the
[accuracy corpus](accuracy.md), nplusone 1.0.0 did not import on SQLAlchemy
2.1.1, and on 2.0.54 it flagged 1 of the 123 scenarios with an N+1.

So moving to SQLAlchemy 2 means moving off nplusone. The table is one small
project on one date, not a full compatibility test of nplusone; the 2.0.54 and
2.1 rows were checked again on 2026-11-08 (2.1.1) with the project below.

## Find and replace

What to change in the source, line by line. The [concept
mapping](#concept-mapping) below explains the differences.

| Find | Replace with |
|---|---|
| `import nplusone.ext.sqlalchemy` | Delete it: the plugin registers itself with pytest |
| `from nplusone.core import profiler` | Delete it |
| An autouse fixture with `with profiler.Profiler():` | Delete the fixture: every test body is measured |
| `profiler.Profiler(whitelist=[...])` in one test's fixture | `@pytest.mark.queryguard_ignore("Order.customer")` on the test |
| `NPLUSONE_RAISE = True` | Nothing: `queryguard_mode = "fail"` is the default |
| `NPLUSONE_RAISE = False` | `queryguard_mode = "warn"` |
| `NPLUSONE_LOG`, `NPLUSONE_LOGGER`, `NPLUSONE_LOG_LEVEL`, `NPLUSONE_ERROR` | Nothing: a finding fails the test, or is a `QueryGuardWarning` in warn mode |
| `NPLUSONE_WHITELIST = [{"label": "n_plus_one", "model": "Order", "field": "customer"}]` | `queryguard_ignore = ["relationship-load Order.customer"]` |
| `{"label": "n_plus_one", "model": "Order"}` (every relationship of a model) | `"relationship-load Order.*"` in `queryguard_ignore` |
| `{"label": "unused_eager_load", "model": "Order", "field": "customer"}` | `"unused-eager-load Order.customer"`; the check is experimental and off until `queryguard_unused_eager_load = "warn"` |
| `with signals.ignore(signals.lazy_load):` | `with pytest_queryguard.ignore("relationship-load"):` |
| `except NPlusOneError` or `pytest.raises(NPlusOneError)` around `Profiler()` | `with record_queries() as log:` and `log.assert_no_nplusone()`, which raises `pytest_queryguard.NPlusOneDetected` |
| `NPlusOne(app)` (Flask) or `NPlusOneMiddleware` (WSGI) | `queryguard.watch(engine)` and `app.wsgi_app = queryguard.QueryGuardWSGI(app.wsgi_app)`, from `pytest_queryguard import app as queryguard` ([in-application mode](in-app.md)) |
| `session.query(Order)` | Can stay: both `session.query()` and `select()` are measured |

## Concept mapping

| nplusone | pytest-queryguard |
|---|---|
| `with profiler.Profiler():` around a test | Nothing: every test body is measured by default |
| `Profiler()` raises `NPlusOneError` | A failing test: `queryguard: N+1 detected` (`queryguard_mode = fail`, the default) |
| `NPLUSONE_RAISE = False` and logging | `queryguard_mode = warn`: a `QueryGuardWarning` and the test passes |
| Label `n_plus_one` | Finding kind `relationship-load` (also `repeated-statement`, `column-load`, `write-loop`) |
| Label `unused_eager_load` | Finding kind `unused-eager-load`, experimental, off by default: `queryguard_unused_eager_load = warn` |
| `NPLUSONE_WHITELIST` | `queryguard_ignore` patterns: `kind label`, with optional `file=` and `function=` |
| `signals.ignore(signals.lazy_load)` block | `with pytest_queryguard.ignore("relationship-load"):` ([the `ignore()` block](ignoring.md#the-ignore-block)), a marker, `@pytest.mark.queryguard_ignore("Order.customer")` (or its alias `allow_nplusone`), or an ini pattern |
| Whitelist patterns with `fnmatch` | The same: `fnmatch` patterns in markers and `queryguard_ignore` |
| A profiler around one block | `record_queries()` and `log.assert_no_nplusone()` |
| Flask, Django, WSGI middleware | Flask and any WSGI or ASGI app: the [in-application mode](in-app.md) (`QueryGuardWSGI`, `QueryGuardASGI`). Django: as a test-suite check only, experimental ([Django](django.md)); no in-application mode |

## Before and after

The same project, the same test, in nplusone's world and in
pytest-queryguard's. The loop is in `orders.py`, the application, and the
test calls it. The "before" run is Python 3.9, SQLAlchemy 1.3.24, nplusone
1.0.0 and pytest 8.4.2. The "after" run is Python 3.15, SQLAlchemy 2.1.1 and
pytest 9.1.1. Both were run on 2026-11-08.

### Before

`conftest.py` turns nplusone on around every test (the `session` fixture,
not shown, creates three customers with one order each and calls
`session.expunge_all()`):

```python
import pytest
from nplusone.core import profiler
import nplusone.ext.sqlalchemy  # noqa: F401  (patches SQLAlchemy)


@pytest.fixture(autouse=True)
def nplusone_profiler():
    with profiler.Profiler():
        yield
```

```python
# orders.py
from models import Order


def customer_names(session):
    return [order.customer.name for order in session.query(Order)]
```

```python
# test_orders.py
from orders import customer_names


def test_customer_names(session):
    assert customer_names(session) == ["c1", "c2", "c3"]
```

```
E           nplusone.core.exceptions.NPlusOneError: Potential n+1 query detected on `Order.customer`
=========================== short test summary info ============================
FAILED test_orders.py::test_customer_names - nplusone.core.exceptions.NPlusOn...
1 failed in 0.09s
```

### After

Delete the profiler fixture and the nplusone imports. Move the models to
SQLAlchemy 2 (`DeclarativeBase`, `Mapped`) and, if you like, the query to
`select()`; the test does not change:

```python
# orders.py
from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Order


def customer_names(session: Session) -> list[str]:
    return [order.customer.name for order in session.scalars(select(Order))]
```

```
queryguard: N+1 detected

  Order.customer was lazy-loaded 3 times
  first at orders.py:8 in customer_names
    called from test_orders.py:5 in test_customer_names
  parents loaded at orders.py:8 in customer_names
  kind: relationship-load  label: Order.customer
  docs: https://maciejziel.github.io/pytest-queryguard/how-detection-works.html#relationship-load
  sql: SELECT customer.id, customer.name FROM customer WHERE customer.id = ?
  fix: load it with the parent query, e.g.
       select(Order).options(joinedload(Order.customer))
=========================== short test summary info ============================
FAILED test_orders.py::test_customer_names - Failed: queryguard: N+1 detected
1 failed in 0.11s
```

The message names the line of the application, the line of the test that
called it, and what to change. With
`select(Order).options(joinedload(Order.customer))` the test passes.

The same project with nplusone 1.0.0 on SQLAlchemy 2.0.54 passes: `1 passed`,
nothing reported.

### Whitelist

Before, in `conftest.py`:

```python
with profiler.Profiler(
    whitelist=[{"label": "n_plus_one", "model": "Order", "field": "customer"}]
):
    yield
```

(the test passes: `1 passed`). After, either in the test:

```python
@pytest.mark.queryguard_ignore("Order.customer")
def test_customer_names(session): ...
```

or for the whole suite, in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
queryguard_ignore = ["relationship-load Order.customer"]
```

Both give `1 passed`.

### Warn first

nplusone logged by default and raised only with `NPLUSONE_RAISE`. To start
with warnings:

```console
$ pytest --queryguard-mode=warn
test_orders.py::test_customer_names
  .../test_orders.py:4: QueryGuardWarning: queryguard: N+1 detected
  ...
1 passed, 1 warning in 0.08s
```

For a project that already has many findings, a [baseline](baseline.md) lets
you turn failing on for new findings only.

### A block instead of a profiler

```python
from orders import customer_names
from pytest_queryguard import record_queries


def test_customer_names_in_a_block(session):
    with record_queries() as log:
        customer_names(session)
    log.assert_no_nplusone()
```

`assert_no_nplusone()` raises `NPlusOneDetected` with the same message. It does
not read the ini options; see [Reading the queries](python-api.md).

## Things that behave differently

- nplusone's `Profiler` measures whatever runs inside its `with` block; with
  an autouse fixture that is the whole test including other fixtures'
  setup. pytest-queryguard measures only the test function body.
- nplusone reports a loop wherever it is. pytest-queryguard by default leaves
  out the N+1s of test code: a loop written in the test function itself, in
  `conftest.py`, in a factory or in a helper under `tests/`. They are counted
  in a `queryguard: test code` section at the end of the run, and
  `pytest -o queryguard_test_code=warn` shows them. A test that only reads
  data in a loop of its own, as many nplusone examples do, passes; move the
  loop into the application, or see [Test code](how-detection-works.md#test-code).
- A lazy load of a many-to-one relationship for an object that is already in
  the session's identity map sends no SQL and is not a finding. In both tools
  the test data has to be loaded fresh (my examples call `session.expunge_all()`
  before the loop).
- The default threshold is 2 different parents, and repeated statements need
  5 runs; both can be changed. See [How detection works](how-detection-works.md).
- Only what I ran is covered here: nplusone's generic `Profiler` on
  SQLAlchemy 1.3.24, 2.0.54 and 2.1. I did not run its Flask, Django or Peewee
  integrations; the settings and the Flask and WSGI rows of the
  find-and-replace table were read in nplusone 1.0.0's installed source.
