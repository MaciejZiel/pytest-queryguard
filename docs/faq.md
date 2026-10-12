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
as through pg8000, on PostgreSQL 17.11; psycopg2 was not run again. Async drivers other than asyncpg (for example psycopg's async mode) are
skipped by the index check, with a notice in the session summary.

## Why was nothing reported for my test?

- the code under test ran in a fixture, not in the test body;
- the statements did not come through SQLAlchemy (a raw DB-API connection);
- `queryguard_mode = off`, or the finding is in the baseline or an ignore
  pattern;
- the count is below the threshold. `--queryguard-report` shows how many
  statements each test ran.

## Does it slow my tests down?

It adds work per statement: a stack walk for the call site and a timer around
the cursor call. `scripts/bench_overhead.py` measures it, and CI compares it
with a stored baseline. The index check adds a few database round trips per
distinct statement per test, about 1 ms per statement in the benchmark in
[Missing-index check](index-check.md); the numbers depend on your machine.

## Does it work with Django, Peewee or raw SQL?

Not with Django's ORM or Peewee: only SQLAlchemy 2 is supported (the
[comparison](comparison.md) lists tools for Django). Raw SQL that
goes through SQLAlchemy (`text()`, Core) is seen; a raw DB-API connection that
SQLAlchemy does not manage is not.
