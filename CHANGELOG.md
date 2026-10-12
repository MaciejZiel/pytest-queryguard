# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Internal: detectors now hand their results to a stable `Finding` model
  (`kind`, `label`, `file`, `function`, `message`, `severity`) before the
  report is rendered. The messages and the public API are unchanged.
- The check that decides whether a stack frame belongs to SQLAlchemy, pytest or
  the standard library is now cached per file name. Measured with
  `scripts/bench_overhead.py`, this cuts the per-statement overhead of the
  measurement by about half.
- A test stores at most 10000 statements and 10000 relationship loads for its
  reports (`queryguard_max_events`, `0` for no limit). Past the limit they are
  only counted: budgets and the session report still use the real count, and
  the budget failure and the N+1 report say that details are missing.

### Fixed

- When a listener raises, the plugin still stops measuring so that it never
  breaks the suite, but it no longer does so silently. The warning names the
  listener or step that failed, and the end of the session prints a
  "queryguard: measurement stopped" section with the listener, the error and
  the line that raised it, once per session, with or without
  `--queryguard-report`.

## [0.3.0] - 2026-10-11

### Added

- The missing-index check now covers async engines on asyncpg. asyncpg
  prepares statements, and PostgreSQL would take the `$n` inside `EXPLAIN`
  as the `EXPLAIN`'s own parameters. So on asyncpg the `EXPLAIN` runs in a
  PL/pgSQL `DO` block on the test's own connection and savepoint, and the
  plan comes back through a transaction-local setting. Findings, notices
  and guarantees are those of sync engines. Other async drivers are still
  skipped with a notice. See `docs/design-0.3.md`.
- On a database without PL/pgSQL, the asyncpg index check now reports one
  notice for the session instead of one failed `EXPLAIN` per statement.
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

- The index check reads the first column of each btree index and the
  `varchar` columns of each table from the catalog once per engine for the
  whole session instead of once per test. Any statement that may change
  them (`CREATE`, `DROP`, `ALTER`, `REINDEX`, `TRUNCATE` and other DDL,
  seen in tests and fixtures alike) clears the cache, as does the end of a
  transaction that ran one. On 100 tests with 10 indexed statements each
  against PostgreSQL 17, the check went from about 2.3 ms to 1.2 ms per
  statement (#5). The same statement is explained again after such DDL, so
  a rolled-back index change no longer hides a finding. Clearing is safe
  while a plan is being read, also from another thread or when a
  connection is garbage collected, and when a two-phase transaction with
  DDL ends. A statement that cannot be planned is counted once in the
  "could not be explained" summary even when DDL made the check retry it.
- `QueryLog.assert_no_nplusone()` is documented as ignoring the
  `queryguard_*` ini options: a project with a custom
  `queryguard_repeat_threshold` passes `repeat_threshold` explicitly.
- Paths in the `NPlusOneDetected` message, and in the `QueryBudgetExceeded`
  message of a `with max_queries(n):` block, are relative to pytest's rootdir,
  as in the plugin's own failures, instead of the working directory (which is
  still the base outside pytest).

### Fixed

- The index check now judges temporary tables: trailing columns of their
  multicolumn indexes and `varchar` casts were silently skipped, because
  PostgreSQL names the schema `pg_temp` in plans and `pg_temp_<n>` in the
  catalog.
- The index check now reports a partial index read in full as a bitmap.
  With an index such as `(id) WHERE deleted_at IS NULL` and
  `WHERE deleted_at IS NULL AND email = ?`, the plan before `ANALYZE` was
  skipped, while the plan after it was reported (#4).

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

[Unreleased]: https://github.com/MaciejZiel/pytest-queryguard/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/MaciejZiel/pytest-queryguard/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/MaciejZiel/pytest-queryguard/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/MaciejZiel/pytest-queryguard/tree/v0.1.0
