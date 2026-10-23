# In-application mode

pytest-queryguard normally runs in your tests. The in-application mode runs the
same N+1 rules and query budgets on every HTTP request of a running ASGI or
WSGI application, so you see findings while you click through a development
server or a staging environment.

> **Development and staging only.** The mode adds work to every statement of
> a watched engine, and the optional panel shows SQL text to anyone who can
> reach it. Never enable either in production. The examples below switch it
> on only when `APP_ENV` is `development`.

It is experimental (see [Stability and versioning](stability.md#experimental)):
names in `pytest_queryguard.app`, their settings and the panel's JSON may still
change in a minor release, without a deprecation period.

## FastAPI and Starlette

Watch the engine, add the middleware. A shortened version of `app/main.py` in
[examples/in-app](https://github.com/MaciejZiel/pytest-queryguard/tree/main/examples/in-app),
which also has the endpoints and a `demo.py` that sends requests to them:

```python
import os

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pytest_queryguard import app as queryguard

engine = create_async_engine("sqlite+aiosqlite:///app.db")
sessions = async_sessionmaker(engine, expire_on_commit=False)
app = FastAPI()

# Development only: never in production.
if os.environ.get("APP_ENV") == "development":
    queryguard.watch(engine)
    app.add_middleware(
        queryguard.QueryGuardASGI,
        budgets={"GET /orders/{order_id}": 1},
        panel=True,
    )
```

`watch()` takes an `Engine` or an `AsyncEngine`. Call it at startup, before the
engine hands out connections. `QueryGuardASGI` is plain ASGI middleware and
does not import Starlette, so it also wraps any other ASGI 3 application:
`app = queryguard.QueryGuardASGI(app)`.

A request to the endpoint with a lazy-load loop logs a WARNING on the
`pytest_queryguard.app` logger. This is the output of `python demo.py` in the
example (FastAPI 0.141, Starlette 1.7, SQLAlchemy 2.1):

```text
WARNING pytest_queryguard.app: queryguard: GET /orders: 6 statements

queryguard: N+1 detected

  Order.customer was lazy-loaded 5 times
  first at app/main.py:48 in list_orders
  kind: relationship-load  label: Order.customer
  sql: SELECT customer.id, customer.name FROM customer WHERE customer.id = ?
  fix: load it with the parent query, e.g.
       select(Order).options(joinedload(Order.customer))
```

`GET /orders/3` runs two statements against a budget of one:

```text
WARNING pytest_queryguard.app: queryguard: GET /orders/3 (route /orders/{order_id}): 2 statements

queryguard: expected at most 1 query, got 2

  1. SELECT orders.id, orders.customer_id FROM orders WHERE orders.id = ?  (app/main.py:64 in get_order)
  2. SELECT customer.id, customer.name FROM customer WHERE customer.id = ?  (app/main.py:65 in get_order)

  database time: 226 us
  slowest:
       127 us  SELECT orders.id, orders.customer_id FROM orders WHERE orders.id = ?  (app/main.py:64 in get_order)
        99 us  SELECT customer.id, customer.name FROM customer WHERE customer.id = ?  (app/main.py:65 in get_order)
```

A request without findings and within its budget logs nothing.

## Flask and other WSGI applications

Wrap `app.wsgi_app`. WSGI has no route templates, so a `before_request` hook
tells queryguard the Flask rule; without it, budgets match the request path.

```python
import os

from flask import Flask, request
from sqlalchemy import create_engine

from pytest_queryguard import app as queryguard

engine = create_engine("sqlite:///app.db")
app = Flask(__name__)

# Development only: never in production.
if os.environ.get("APP_ENV") == "development":
    queryguard.watch(engine)
    app.wsgi_app = queryguard.QueryGuardWSGI(
        app.wsgi_app, budgets={"GET /orders/<int:order_id>": 1}
    )

    @app.before_request
    def name_the_route() -> None:
        # WSGI has no route templates; tell queryguard the Flask rule.
        if request.url_rule is not None:
            queryguard.set_route(request.url_rule.rule)
```

The same lazy-load loop in a Flask view:

```text
WARNING pytest_queryguard.app: queryguard: GET /orders: 6 statements

queryguard: N+1 detected

  Order.customer was lazy-loaded 5 times
  first at app/flask_app.py:41 in list_orders
  kind: relationship-load  label: Order.customer
  sql: SELECT customer.id, customer.name FROM customer WHERE customer.id = ?
  fix: load it with the parent query, e.g.
       select(Order).options(joinedload(Order.customer))
```

A WSGI request ends when its response body has been read to the end or closed,
as every WSGI server does. Iterator initialization and response cleanup run in
that request's context. When the body is exhausted, the middleware closes it
before finalizing the report, so queries in cleanup callbacks (such as Flask's
`Response.call_on_close`) count too. A later `close()` does not run those
callbacks again. Because of that early close, an exception raised by the
application's `close()` reaches the server from the last step of iteration
instead of from the server's own `close()` call; the report is finalized either
way. The Flask test client does neither on its own: read the body
(`response.data`, `response.get_json()`), use
`with client.get(...) as response:`, or pass `buffered=True`.

## Outside HTTP

`track()` measures any block, for a job, a command or a message handler:

```python
from pytest_queryguard import app as queryguard

with queryguard.track("nightly export", default_budget=50) as report:
    ...  # run the job
print(report.statement_count, [f.label for f in report.findings])
```

## Settings

`QueryGuardASGI`, `QueryGuardWSGI` and `track()` take these as keyword
arguments, or one `queryguard.Settings(...)` object as `settings=`.

| Setting | Default | Meaning |
|---|---|---|
| `relationship_threshold` | 2 | Lazy loads of one relationship for different parents that count as N+1 |
| `repeat_threshold` | 5 | Runs of one `SELECT` shape that count as N+1 |
| `column_load_threshold` | 5 | Instances loading deferred or expired columns on their own |
| `small_repeat_threshold` | None | Optional; as in the plugin |
| `write_loop_threshold` | None | Optional; as in the plugin |
| `repeat_scope` | `"request"` | `"request"`: everything in a request counts together; `"session"`: each Session on its own |
| `budgets` | `{}` | `"METHOD path"` or `"path"` to the most statements a request may run |
| `default_budget` | None | The budget of a request no key matches |
| `report` | `"log"` | `"log"`, `"warn"`, a callable, or a list of them |
| `max_events` | 1000 | Statements stored per request; more are counted, not stored |

The rules are the plugin's; see [How detection works](how-detection-works.md).
The missing-index check does not run in the application.

**Budgets.** A key's path is an `fnmatch` pattern matched against the route
template (`/orders/{order_id}` in FastAPI and Starlette, the rule given to
`set_route()` in Flask) and against the request path; the first matching key
wins, so put specific keys first: `{"GET /orders/{order_id}": 2, "/admin/*": 50}`.
In an application mounted inside another (`app.mount("/api", sub_app)`), the
template is the one inside the mounted application, without the mount prefix;
the request path keeps it.

**Reports.** `report` is used only for requests with findings or over their
budget. `"log"` writes one WARNING record. `"warn"` issues
`QueryGuardWarning`, which follows Python's warning filters: with
`-W error::pytest_queryguard.QueryGuardWarning` it becomes an exception,
raised when the request ends, and only when the application did not raise
its own: the application's exception always wins, and the other targets
still run. A callable receives a `RequestReport` with `method`, `path`, `route`, `status`,
`statements` (`Query` objects with `sql`, `callsite`, `relationship` and
`duration`), `statement_count`, `db_time`, `duration`, `findings`, `budget`,
`over_budget` and `message`:

```python
from pytest_queryguard import app as queryguard


def to_my_tracker(report: queryguard.RequestReport) -> None:
    for finding in report.findings:
        print(report.method, report.route or report.path, finding.kind, finding.label)


middleware_options = {"report": ["log", to_my_tracker]}
```

An exception in a callable is logged and does not reach the request.

## The panel

`panel=True` serves two pages from the middleware itself, for the last
`panel_history` requests (default 50):

- `/_queryguard/`: a self-contained HTML page (inline CSS and script, no
  third-party code) with each request's statements, timings and findings;
- `/_queryguard/requests.json`: the same data as JSON, newest first.

`panel_path` moves both. By default the panel answers only loopback clients
(`127.0.0.0/8`, `::1`); `panel_loopback_only=False` lifts that. This guards
against exposing it by accident and is not access control: behind a reverse
proxy every client is the proxy. The responses carry a strict
`Content-Security-Policy` and `Cache-Control: no-store`, and the page writes
every value as text, never as HTML. Bound parameter values are never stored
in the panel, but the SQL text is, including any literal the application puts
into it. The panel is off by default; never enable it in production.

The demo prints the panel's JSON for its three FastAPI requests:

```text
--- the panel's requests.json, newest first ---
GET /orders/3 200 2 statements budget 1 []
GET /orders-fixed 200 2 statements budget None []
GET /orders 200 6 statements budget None ['Order.customer']
```

## Concurrent requests

Each request's statements go to a collector held in a `contextvars.ContextVar`,
so concurrent requests never mix:

- asyncio tasks get a copy of the context, so tasks a request starts count for
  it, and other requests' tasks do not;
- SQLAlchemy runs an `AsyncSession`'s work in a greenlet that shares the
  awaiting task's context;
- Starlette runs sync endpoints with `run_in_threadpool`, and
  `asyncio.to_thread()` copies the context too, so their statements count.

A thread you start yourself (`threading.Thread`, a `ThreadPoolExecutor`)
starts with an empty context: its statements are not counted for any request,
unless you run it with `contextvars.copy_context().run(...)`. Starlette
background tasks run before the ASGI call returns, so their statements count
for the request that scheduled them.

Python 3.14 can start threads with a copy of the creating thread's context
instead. That is the default on free-threaded builds (3.14t) and can be turned
on with `-X thread_inherit_context=1` or `PYTHON_THREAD_INHERIT_CONTEXT=1`;
`sys.flags.thread_inherit_context` tells which applies. Then a thread you start
inside a request counts for that request. A pool's worker keeps the context of
the request whose `submit()` created it (workers start lazily, so a pool
created at import time is affected too), for every later task: while that
request is still running, statements from other requests' tasks are counted for
it, and once it has ended they are not counted at all. To attribute pool work
correctly on every build, submit each task in a copy of the current context:

```python
ctx = contextvars.copy_context()
pool.submit(ctx.run, work, arg)
```

`tests/inapp/` checks this with 50 requests in `asyncio.gather`, through
`httpx.AsyncClient`, in a thread pool, and with tasks and threads mixed in one
request.

## Errors and cost

An internal error never reaches the application. The first one logs an ERROR
record on `pytest_queryguard.app` and switches the in-app mode off for the
rest of the process; `queryguard.internal_error()` returns it. It is separate
from the pytest plugin's own error state, so neither switches the other off.

Importing `pytest_queryguard.app` or creating the middleware registers
nothing; only `watch()` adds listeners, to that engine and, while any engine
is watched, to `Session` for the ORM events. `unwatch(engine)` removes them.
`python scripts/bench_overhead.py --app` times a statement with the engine not
watched, watched but outside a request, and inside one. On an in-memory SQLite
database, on a machine that was busy with other work (load average 20 to 90
on 16 cores; minimum of 21 runs, microseconds per statement):

| Statement | Not watched | Watched, outside a request | Inside a request |
|---|---|---|---|
| Core `SELECT` | 41.7 | 46.4 (+4.7) | 53.2 (+11.5) |
| ORM `select()` | 74.3 | 93.9 (+19.6) | 96.1 (+21.9) |

Treat these as an order of magnitude and measure your own application; see
[Performance](performance.md) for the method.
