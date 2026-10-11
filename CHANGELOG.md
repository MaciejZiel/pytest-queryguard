# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `pytest_queryguard.record_queries()`, a context manager that records the
  SQL statements a block sends and yields a `QueryLog`. The log has the
  statements as `Query` objects (`sql`, `callsite`, `relationship`), their
  `count`, and `assert_no_nplusone()`, which applies the N+1 rules to the
  block and raises `NPlusOneDetected`. `Query`, `QueryLog`, `CallSite` and
  `NPlusOneDetected` are exported for type hints. It works in pytest and
  outside it, and changes nothing else. A `QueryLog` is also sized, iterable and
  indexable (`len()`, `for`, `log[i]`, `log[a:b]`) but not a
  `collections.abc.Sequence`.
- `QueryGuardInternalWarning` (exported since 0.2.0) is now documented. A
  `record_queries()` block that ends after an internal error (including one
  recorded before the block, which had already stopped the recording) issues
  it, saying that the log may be incomplete, and
  `QueryLog.assert_no_nplusone()` raises `RuntimeError` instead of passing
  on such a log. This also applies outside pytest and with
  `--queryguard-mode=off`, where the plugin itself reports nothing.

### Changed

- `QueryLog.assert_no_nplusone()` is documented as ignoring the
  `queryguard_*` ini options: a project with a custom
  `queryguard_repeat_threshold` passes `repeat_threshold` explicitly.
- Paths in the `NPlusOneDetected` message, and in the `QueryBudgetExceeded`
  message of a `with max_queries(n):` block, are relative to pytest's rootdir,
  as in the plugin's own failures, instead of the working directory (which is
  still the base outside pytest).

## [0.2.0] - 2026-10-11

### Added

- Missing-index check for PostgreSQL 16 and newer, off by default. With
  `--queryguard-index-check` or `queryguard_index_check = true`, each
  distinct `SELECT` in a test body is planned with
  `EXPLAIN (GENERIC_PLAN, VERBOSE, FORMAT JSON)` and `enable_seqscan` off,
  inside a savepoint on the statement's own connection. The test fails (or
  warns, in `warn` mode) when the plan reads a whole table to apply a
  filter, walks a whole index for an equality filter, or uses an index
  condition that leaves out a btree index's first column (PostgreSQL 16
  and 17). The message names the table, the condition, the SQL and the call
  site and, when the column is obvious, gives a `CREATE INDEX` hint.
- `--no-queryguard-index-check` to turn the check off for one run.
- `queryguard_index_ignore_tables` ini option and
  `@pytest.mark.allow_missing_index` marker to exclude small lookup tables
  or whole tests from the index check.
- A session summary for statements the index check could not check:
  other databases, PostgreSQL before 16, async engines, statements
  PostgreSQL could not plan, and a check stopped by a broken savepoint.
- CI job that runs the test suite against PostgreSQL 15, 16 and 17.

### Changed

- `IN` lists whose placeholders carry multi-word, schema-qualified or
  quoted type casts (`$1::DOUBLE PRECISION`, `$1::public.mood`) or use
  `CAST(? AS INTEGER)` now collapse to one form for N+1 detection.

## [0.1.0] - 2026-10-11

First release.

### Added

- Automatic N+1 detection for SQLAlchemy 2 in every test, with no
  configuration. Two rules: one relationship lazy-loaded for several parent
  objects, and one normalized `SELECT` repeated several times.
- Failure messages that point to the line of application code that caused
  the N+1, show the SQL involved and, for relationship loads, suggest a
  `selectinload` fix.
- `@pytest.mark.max_queries(n)` marker (also `max_queries(n=...)`) that fails
  a test running more than `n` SQL statements and lists every statement with
  its call site.
- `pytest_queryguard.max_queries()` context manager, which raises
  `QueryBudgetExceeded` and also works outside pytest.
- `@pytest.mark.allow_nplusone` marker to skip N+1 detection for one test.
- Ini options `queryguard_mode` (`fail`, `warn`, `off`),
  `queryguard_relationship_threshold` and `queryguard_repeat_threshold`,
  readable from `[tool.pytest.ini_options]`, `pytest.ini`, `setup.cfg` and
  pytest 9's native `[tool.pytest]` table.
- `--queryguard-mode` command-line option that overrides the ini value.
- `--queryguard-report` command-line option that prints the ten tests with
  the most queries at the end of the session.
- Support for `AsyncSession`, FastAPI's `TestClient` and code that queries
  from other threads.
- Only the test function body is measured; fixture setup and teardown are not.
- An internal error disables the plugin with a single warning instead of
  failing the test suite.

[Unreleased]: https://github.com/MaciejZiel/pytest-queryguard/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/MaciejZiel/pytest-queryguard/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/MaciejZiel/pytest-queryguard/tree/v0.1.0
