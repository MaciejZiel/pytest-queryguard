# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Missing-index check for PostgreSQL 16 and newer, off by default. With
  `--queryguard-index-check` or `queryguard_index_check = true`, each
  distinct `SELECT` in a test body is planned with
  `EXPLAIN (GENERIC_PLAN, VERBOSE, FORMAT JSON)` and `enable_seqscan` off,
  inside a savepoint on the statement's own connection. A plan that still
  reads a whole table for a filter or a join condition fails the test (or
  warns, in `warn` mode) with the table, the condition, the SQL, the call
  site and, when the column is obvious, a `CREATE INDEX` hint.
- `queryguard_index_ignore_tables` ini option and
  `@pytest.mark.allow_missing_index` marker to exclude small lookup tables
  or whole tests from the index check.
- A session summary for statements the index check could not check:
  other databases, PostgreSQL before 16, async engines and statements
  PostgreSQL could not plan.
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

[Unreleased]: https://github.com/MaciejZiel/pytest-queryguard/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/MaciejZiel/pytest-queryguard/tree/v0.1.0
