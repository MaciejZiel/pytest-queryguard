# FAQ

## How does this relate to SQLAlchemy's `raiseload`?

`raiseload` is a loader option (`relationship(lazy="raise")`, or
`.options(raiseload(Order.customer))` per query) that makes SQLAlchemy raise
`InvalidRequestError` the moment code touches a relationship that was not
loaded. It prevents lazy loading everywhere the option applies, in tests and in
production, and it needs a change in the model or the query.

pytest-queryguard changes nothing in your code. It watches the statements
a test sends and reports patterns, including ones `raiseload` does not
cover: the same statement repeated in a loop with `session.get()`, Core and
`text()` statements, loops over deferred or expired columns, row-by-row writes
and missing indexes. The two work together: `raiseload` blocks a lazy load, so
a blocked access raises instead of reaching the database, and queryguard has
no lazy load to count at that point. Use `raiseload` where you want a hard
rule in the model, and queryguard where you want to find the places that need
one.

## Two parents each lazy-load one relationship. Is that really a finding?

Yes, by default. The relationship rule counts distinct parent objects, and the
default threshold is 2, so `order_a.customer` and `order_b.customer` loaded
separately is reported as `Order.customer was lazy-loaded 2 times`. That is the
smallest N+1, and test databases hold a few rows, so a higher default would
miss loops in tests that only create a couple of objects. If two is too strict
for your code, raise it:

```toml
[tool.pytest.ini_options]
queryguard_relationship_threshold = 3
```

or silence one place, as in the next question.

## A finding is a false positive. How do I silence it?

From the narrowest to the widest:

- one finding, in one test: `@pytest.mark.allow_nplusone("Order.customer",
  function="list_orders")`;
- one finding, in the whole suite: a line in `queryguard_ignore`, for example
  `relationship-load Order.customer function=list_orders`;
- a finding you cannot fix yet: put it in a [baseline](baseline.md);
- a kind you do not want to fail tests: `queryguard_fail_on`, which makes the
  other kinds warnings;
- a table that is fine to scan (index check): `queryguard_index_ignore_tables`
  or `@pytest.mark.allow_missing_index`.

See [Ignoring findings](ignoring.md). Patterns that match nothing are listed
as stale ignores, so they do not outlive the code.

## My integration test makes several requests and fails with repeated-statement

By default the repeat rules count everything one test runs. A test that calls
an API five times, with a test client for example, runs the same lookups
(authentication, the organisation, a webhook) once per request, each in its own
SQLAlchemy `Session`. Nothing loops, but the count is five. The failure says so
when it can: `ran 9 times across 8 different Sessions or connection checkouts`,
with a note naming the option. Set it to count each `Session` on its own:

```toml
[tool.pytest.ini_options]
queryguard_repeat_scope = "session"
```

A loop inside one request, or any code that runs the same statement five times
in one `Session`, is still reported. The same applies to lazy loads, column
loads and write loops. See [Tests with several sessions or
requests](several-sessions.md) for how the scope is chosen and where it stops
being exact.

## Does it work with pytest-xdist?

Yes for detection, budgets, the baseline and SARIF: each worker measures its
own tests, and the controller merges what the workers saw. The session report
(`--queryguard-report`) needs one process and prints
`queryguard report: not available with pytest-xdist`. The summary sections of
the index check and the stale-ignore list are printed by the process that runs
the tests, so they are not shown under xdist. The test suite runs the plugin
under `-n 2` and `-n 4` in CI.

## Which PostgreSQL drivers work?

N+1 detection and budgets hook SQLAlchemy events, not a driver, so they do not
depend on the driver. The index check needs a driver it can talk to through
SQLAlchemy: CI runs it with pg8000 (sync) and asyncpg (async), on PostgreSQL 14
to 18. psycopg2 and psycopg 3 are LGPL-licensed, so the project does not
depend on them and does not run them in CI. They were checked by hand during
review and gave the same findings for plain statements, `%` in SQL, expanding
`IN`, autocommit and `stream_results`; this is not covered by an automated
test. On 2026-10-12 the 14 index scenarios of the
[comparison](comparison.md) also gave the same results through psycopg 3 (3.3.6)
as through pg8000, on PostgreSQL 17.11; psycopg2 was not part of that run.
Async drivers other than asyncpg (for example psycopg's async mode) are skipped
by the index check, with a notice in the session summary.

## Why was nothing reported for my test?

- the code under test ran in a fixture, not in the test body;
- the statements did not come through SQLAlchemy (a raw `sqlite3` or other
  DB-API connection, the Django ORM, Peewee, Tortoise ORM): the plugin does not
  see them, and `--queryguard-report` shows 0 statements for the test;
- `queryguard_mode = off`, or the finding is in the baseline or an ignore
  pattern;
- the count is below the threshold. `--queryguard-report` shows how many
  statements each test ran.

## Does it slow my tests down?

It adds work per statement: a stack walk for the call site and a timer around
the cursor call. `scripts/bench_overhead.py` measures it, and CI compares it
with a stored baseline.

Measured with `scripts/bench_overhead.py` on an Intel Core i5-13500H, Python
3.13, SQLAlchemy 2.1.1, median of 15 runs of 5000 statements pinned to one core,
each configuration in a fresh process, against an **in-memory SQLite** database:

| Per statement | Measurement off | On | Added |
|---|---|---|---|
| Core `select` by key | 41 us | 50 us | +9 us (+22 %) |
| ORM `select` by primary key | 76 us | 107 us | +31 us (+40 %) |

Two rounds differ by a few points, so read these as "about a fifth to two
fifths" and not as exact figures. The percentages are large because an
in-memory SQLite statement is almost free: the added cost is a fixed 9 to 31
microseconds, and a statement that goes to a real database takes far longer, so
the share is much smaller there. These are micro-benchmarks. On a generated
suite of 300 tests against a file-based SQLite database and against PostgreSQL
the difference between running with and without the plugin was smaller than the
run-to-run noise; see [Performance](performance.md) for the method and numbers.

The options that are off by default cost nothing until you turn them on:

- The missing-index check (PostgreSQL) adds a few database round trips per
  distinct statement per test, about 1 ms per statement in the benchmark in
  [Missing-index check](index-check.md). On SQLite it adds, over the "on" column
  above, +13.5 us for a repeated statement shape, +31.5 us for a repeated ORM
  statement and +34.5 us for a new shape that must be explained
  (`scripts/bench_overhead.py --index-check`).
- Eager-load tracking (`queryguard_unused_eager_load`) wraps attribute reads:
  171 ns per attribute read with the option off, 207 ns with it on but idle
  (outside a test body), 306 ns while a test body is tracked
  (`scripts/bench_eager_overhead.py`).

The numbers depend on your machine and database.

## Does it work with Django, Peewee or raw SQL?

Not with Django's ORM, Peewee or Tortoise ORM: only SQLAlchemy 2 is supported (the
[comparison](comparison.md) lists tools for Django). Raw SQL that
goes through SQLAlchemy (`text()`, Core) is seen; a raw DB-API connection that
SQLAlchemy does not manage is not.
