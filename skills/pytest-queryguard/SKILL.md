---
name: pytest-queryguard
description: Find, fix and prevent N+1 queries and other repeated SQL in SQLAlchemy 2 or Django ORM code with the pytest plugin pytest-queryguard, then prove the fix by re-running the tests. Use when the user mentions N+1 queries, "too many queries", a lazy load in a loop, slow ORM endpoints or serializers, select_related/prefetch_related/joinedload/selectinload, counting or limiting SQL queries in a test (assertNumQueries, django_assert_num_queries, a query count test, a query budget), or an alternative to nplusone or django-zeal; after changing code that loads ORM objects in a project that uses pytest; or when a test fails with a queryguard "N+1 detected" report.
---

# Finding and fixing N+1 queries with pytest-queryguard

pytest-queryguard is a pytest plugin: once installed, every test fails when
its code runs an N+1 query, with no marker or code change. It measures; do
not guess from reading the code when you can run the tests. It supports
SQLAlchemy 2 (sync, async, FastAPI, Flask-SQLAlchemy, SQLModel) and,
experimentally, the Django ORM next to pytest-django.

## 1. Check that it is installed

Use the project's own environment (`uv run`, `poetry run`, the `.venv`):

```console
python -m pytest --help | grep queryguard-json
```

Nothing printed: it is not installed. Add it to the project's test
dependencies only if the user agrees: `pytest-queryguard` for SQLAlchemy,
`pytest-queryguard[django]` (with pytest-django) for Django.

## 2. Run the tests that cover the code

Run the tests for the code you changed, not the whole suite first:

```console
python -m pytest tests/test_orders.py --queryguard-json=.queryguard.json --queryguard-write-patch=.queryguard.patch -q
```

Read `.queryguard.json`, not the terminal text. For each test in `tests`,
each entry of `findings` has:

- `kind`: `relationship-load` (a lazy relationship loaded once per row),
  `repeated-statement` (the same SELECT in a loop), `column-load`,
  `write-loop`, `cartesian-load`, ...
- `label`: what repeated, e.g. `Order.customer`;
- `file`, `line`, `function`: the line of application code that sent the
  first statement; `stack`: the callers up to the test;
- `count`: how many times;
- `message`: the full report, whose `parents loaded at` line is the query
  to change and whose `fix:` line is the suggested query.

`"complete": false` means the run stopped early: fix that first.

No finding, but the run ends with a `queryguard: test code` section: the
loop is in test code (the test function, `conftest.py`, a factory, a helper
under `tests/`), whose N+1s are left out by default. To reproduce an N+1 in
a new test, put the loop in the application and call it from the test; to
see the test code's findings, add `-o queryguard_test_code=warn`.

## 3. Fix

- `relationship-load`: add the loader option on the query that loaded the
  parents (the `parents loaded at` line), not in the loop.
  SQLAlchemy: `joinedload(Order.customer)` for a many-to-one or one-to-one,
  `selectinload(Order.items)` for a collection, chained for a path:
  `selectinload(Order.items).joinedload(Item.product)`.
  When `.queryguard.patch` is not empty, `git apply --check .queryguard.patch`
  then `git apply .queryguard.patch` from pytest's rootdir adds exactly that.
  Django: `select_related("author")` for a foreign key or one-to-one,
  `prefetch_related("books")` for a reverse or many-to-many relation,
  `"author__publisher"` for a chain.
- `repeated-statement`: one query with `WHERE id IN (...)` or a join
  instead of one query per item (Django: `in_bulk(ids)`,
  `filter(pk__in=ids)`).
- `column-load` after `commit()`: `expire_on_commit=False` on the session,
  or load the objects again with one query.
- Anything else: follow the `fix:` line of `message` and the `docs` link
  printed in the terminal report.

Do not silence a finding (`ignore()`, the `queryguard_ignore` marker, a
baseline entry, a higher threshold) unless the user asks: it hides the
problem instead of fixing it.

## 4. Re-run and compare

Run the same command again. Done when the finding is gone and the tests pass.
A fix that loads too much shows up as `unused-eager-load` (when enabled);
drop that option.

## Asked for a query count test

For "assert the number of queries" (SQLAlchemy has no `assertNumQueries`):
`@pytest.mark.max_queries(n)` on the test, or `with max_queries(n):` (from
`pytest_queryguard import max_queries`) around the call; the `queryguard`
fixture's `num_queries(n)` asserts an exact count. The failure lists every
statement with its call site. Prefer the N+1 check to a hand-picked number:
it needs no number and fails when the count grows with the rows.

## Without running tests

`python -m pytest_queryguard.lint --models app.models --format json app/`
reads SQLAlchemy code without a database and reports loops over a query
that read a lazy relationship. It is an early warning that misses N+1 queries crossing
functions; the tests measure. With the MCP server
(`python -m pytest_queryguard.mcp`), the same steps are the tools
`run_tests` and `lint`.

Documentation: https://maciejziel.github.io/pytest-queryguard/agents.html
