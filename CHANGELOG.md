# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
