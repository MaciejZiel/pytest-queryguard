# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- A lazy load keeps its relationship when another statement runs between the
  load and its `SELECT`: statements of other `AsyncSession`s running
  concurrently (`asyncio.gather`), the `BEGIN` of SQLAlchemy's pysqlite recipe
  or any statement of a `begin` or `after_begin` listener. The attribution is
  now parked on the `Session` that issued the load instead of on the thread, so
  these loops are no longer reported a second time as a repeated statement.
  Fixes #97 and #99.

- A statement that starts with a comment (sqlcommenter tags, a `-- note` in a
  `text()`) is now classified like the statement after the comment. A loop of
  commented single-row `INSERT`s was reported as a repeated statement with the
  advice to fetch the rows in one query, and the write-loop rule never saw it.
  Fixes #96.

- `--queryguard-update-baseline` no longer rewrites the baseline, and
  `--queryguard-sarif` no longer writes the file, after an interrupted run (a
  collection error, Ctrl-C, a usage error or an internal pytest error). The
  summary says why. Fixes #95.

- Tests: the public API snapshot recorded a wrong return type for
  `QueryLog.slowest()` and did not assert it. It now records
  `tuple[Query, ...]` and checks every member of `QueryLog` the same way.

- PostgreSQL index check: `NOT IN (subquery)` on PostgreSQL 17 and 18 no
  longer gets `fix: CREATE INDEX ON t (col)` for the compared column, which was
  wrong even for a primary key
  ([#83](https://github.com/MaciejZiel/pytest-queryguard/issues/83)). Plan
  conditions with `CASE`, `AT TIME ZONE`, `EXTRACT(... FROM ...)`, `count(*)`,
  `COLLATE` or an array subscript are now parsed instead of matched as text, so a
  column inside them is no longer suggested as a plain index and gets the
  expression-index note
  ([#84](https://github.com/MaciejZiel/pytest-queryguard/issues/84)). A row
  comparison such as `(a, b) > ($1, $2)` no longer asks for an expression index
  ([#85](https://github.com/MaciejZiel/pytest-queryguard/issues/85)).

### Changed

- The declared minimum SQLAlchemy is now 2.0.13 (`sqlalchemy>=2.0.13`). The
  suite and the async helpers need `AsyncAttrs`, which does not exist in
  earlier 2.0 releases, so the old `>=2.0` bound promised more than was tested.

- The options with a fixed set of values now agree on case. `queryguard_mode`,
  `queryguard_hypopg` and `--queryguard-mode` used to reject `FAIL`, `ON` and
  `Warn` while `queryguard_unused_eager_load` and `queryguard_repeat_scope`
  accepted any case; all of them now ignore case and surrounding spaces, and
  unknown values are still a usage error.

- Lower the per-statement overhead by about a sixth in the in-memory SQLite
  micro-benchmark: one shared `CallSite` per source location, one write check
  per statement and a cache for `normalize()`. Behaviour is unchanged. New
  `docs/performance.md` and `scripts/bench_suite.py` (a whole-suite benchmark
  for file-based SQLite or PostgreSQL).

- The accuracy corpus has 23 more scenarios (133 in all): GraphQL-style
  resolvers and DataLoaders, `asyncio.gather` and Celery-style fan-out,
  caching repositories, `with_for_update`, nested transactions, `yield_per`,
  large `selectinload`, ORM bulk statements, upserts, and BUG scenarios that
  look like pagination, batching or polling. `docs/accuracy.md` and the release
  gate's minimum scenario count are updated; the plugin is unchanged.

- Tests and CI: a release gate (`.github/workflows/release-gate.yml`,
  `scripts/release_gate.py`) that checks the test matrix, the accuracy
  minimums, the documentation examples, clean installs, the upgrade from the
  previous release, the overhead budget, the package contents and the
  changelog before a release is tagged. See `docs/releasing.md`.

### Fixed

- PostgreSQL index check: a server newer than the tested releases (16 to 18)
  is still checked and now gets one notice in the session summary that its
  version is untested. Older servers keep their one "not checked" notice. The
  tested versions are listed in `docs/index-check.md`.
- Documentation: the index check page was published as raw Markdown from the
  partial-index paragraph on, including its SQLite section
  ([#117](https://github.com/MaciejZiel/pytest-queryguard/issues/117)). A test
  now fails the build for any page left like that.

## [0.4.0rc2] - 2026-10-12

### Added

- Community files: `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`
  (Contributor Covenant 2.1), issue forms and a pull request template.
- The documentation site has a description, canonical URL and Open Graph tags
  on every page, a favicon, `sitemap.xml`, `robots.txt`, a social preview
  image, a client-side search (no third-party code), "Edit this page" links
  and Previous/Next links. Every page needs a `description` in
  `docs/site.toml`; the strict build fails without one.
- A "Recipes" section in the documentation: FastAPI (`TestClient` and
  `httpx.AsyncClient` with dependency-overridden sessions), Flask-SQLAlchemy,
  SQLModel, Litestar, pytest-asyncio configuration pitfalls, budgets for hot
  endpoints, adopting the plugin on a legacy codebase and CI. A new example,
  `examples/fastapi-dependency-override`, is run by the test suite.
- Missing-index check for MySQL and MariaDB, behind the same switch
  (`queryguard_index_check`, `--queryguard-index-check`). Each distinct
  `SELECT`, `UPDATE` and `DELETE` is explained with `EXPLAIN` and the real bound
  parameters (never `ANALYZE`); a table read in full (`type: ALL`, or an index
  read in full on a column that leads no index) although the statement filters
  or joins on its columns is a `missing-index` finding with a `CREATE INDEX`
  hint and an Alembic call. The suggestion is not verified: neither server has
  hypothetical indexes. Tested with PyMySQL and aiomysql on MySQL 8.4 and
  MariaDB 11.8 on a developer machine; CI does not run a MySQL or MariaDB
  server. See `docs/index-check-mysql.md`.

### Changed

- Tests: an accuracy corpus under `tests/corpus`: 110 small scenarios of realistic ORM
  code (views, serializers, FastAPI, Flask-SQLAlchemy, SQLModel, repositories,
  pagination, bulk writes, async), each marked BUG or CLEAN, run through the
  plugin in its default configuration and with each opt-in rule. Limitations it
  found are in `docs/limitations.md`; `scripts/accuracy_report.py` prints the
  precision and recall.

### Fixed

- A lazy load or column load that autoflushed pending changes first was
  attributed to the flush's write: the UPDATE or DELETE carried the relationship
  name and the load's SELECT carried none, so one loop was also reported as a
  repeated statement. Writes no longer take the pending attribution (#59).
- Documentation: the limitations page, the FAQ and the README now say that a
  raw `sqlite3` or other DB-API connection and non-SQLAlchemy ORMs (Django ORM,
  Peewee, Tortoise ORM) are not seen, and that nothing reports it; the
  limitations page also points to the repeat-scope limits.
- The SQLite index check no longer says a table "is read in full" when the
  plan walks an existing index that the filter cannot use (`SCAN INDEX ix_...`).
  It says the table scans the whole index and that the filter does not match
  the start of any index; the suggested `CREATE INDEX` is unchanged.
- A SARIF result merged from several tests no longer shows one test's count in
  `message.text` next to the total in `properties.count`. The message now
  starts with `Seen in N tests: T times in total, up to M times in one test`,
  and `properties` gains `tests` and `peakCount` for such results. `count`,
  the fingerprints and the baseline identity are unchanged. See
  `docs/sarif.md`.
- The `repeated-statement` hint no longer says to fetch the rows with
  `WHERE id IN (...)` when the statement already uses `IN (...)`. It says the
  batched query ran several times and should be called once, or the calls
  combined.
- Added `queryguard_repeat_scope` (`test` or `session`, default `test`) and
  `QueryLog.assert_no_nplusone(repeat_scope=...)` for false positives in tests
  that make several requests. The repeat, relationship,
  column-load and write-loop rules counted over the whole test, so a test that
  made several independent requests (a test client, for example, with one
  `Session` per request) reported the same authentication or organisation
  lookup as a loop. With `session`, each `Session`, or each connection checkout
  for statements no `Session` ran, is counted on its own. The default is
  unchanged, but a finding whose count spans several Sessions or checkouts now
  says so and names the option. See `docs/several-sessions.md`.
- SARIF locations are now relative to the repository root when
  `GITHUB_WORKSPACE` is set and the pytest rootdir is inside it. Before, they
  were relative to the rootdir, so with the action's `working-directory` set to
  a subdirectory GitHub created alerts on paths that do not exist and showed no
  pull request annotations. Fingerprints and baseline identities are
  unchanged. See `docs/sarif.md`.
- The action skips the SARIF upload for runs triggered by Dependabot (read-only
  token), and a refused upload no longer fails the job.

## [0.4.0rc1] - 2026-10-12

Release candidate: install it with `pip install --pre pytest-queryguard`.

This release adds baselines, SARIF output and a GitHub Action, per-finding
ignores, new finding kinds (column loads, write loops, unused eager loads), query
timing and `max_db_time`, an index advisor, and a missing-index check for SQLite.
It also adds a documentation site and runnable examples. Three things can change the
result or the output of an existing test run without any new option: the `column-load`
kind is on by default, the index advisor is on by default with the index check, and
the index check now also runs on SQLite. They are listed first under "Changed" as
behavior changes, with how to turn them off, as the pre-1.0 clause of the
[stability policy](docs/stability.md) requires.

### Added

**Missing indexes**

- Missing-index check for SQLite, behind the same switch as PostgreSQL
  (`queryguard_index_check`, `--queryguard-index-check`). Each distinct
  `SELECT`, `WITH`, `UPDATE` and `DELETE` in a test body is explained with
  `EXPLAIN QUERY PLAN` and its real bound parameters; the statement is not run,
  so data and transaction are untouched. A scanned table that the statement
  filters or joins on, or an automatic index, is a `missing-index` finding with
  a `CREATE INDEX ix_<table>_<columns> ON <table> (<columns>)` hint. `LIKE`,
  views, `ORDER BY` without a filter, primary keys and unrecognized plan shapes
  are left alone. Works with `sqlite3`, aiosqlite, SQLAlchemy ORM and Core, and
  pytest-xdist; findings honor `queryguard_ignore`, `allow_missing_index`,
  `queryguard_index_ignore_tables`, the baseline and SARIF. See
  `docs/index-check-sqlite.md`. `scripts/bench_overhead.py --index-check` times it.
- Index advisor. Each missing-index finding now suggests an index: a
  `CREATE INDEX` built from the equality, range and `ORDER BY` columns of the
  plan node, a partial index when a constant predicate is selective, and the
  Alembic `op.create_index` call. When the hypopg extension is installed, the
  suggestion is verified inside the check's savepoint: whether the planner
  would use the index and the statement's estimated cost before and after;
  hypothetical indexes are reset, also after errors, on sync drivers and
  asyncpg. No new index is suggested when the table already has one for these
  columns (invalid, partial, or valid but unused), and the finding says why.
  Without hypopg the suggestion stays and one notice says how to install it.
  New options `queryguard_index_advisor` (default `true`, used with the index
  check) and `queryguard_hypopg` (`auto`, `on` or `off`). Findings carry the
  suggestion in an additive `advice` field. See `docs/index-advisor.md`.

**New findings and rules**

- Column-load detection, a new finding kind `column-load`. Instances that each
  load a deferred column, or whose attributes were expired by `commit()`,
  `expire()` or `expire_all()`, on their own are reported with the right
  advice: `undefer(Model.column)` (or removing `defer()`, `load_only()` or
  `deferred()`), or `expire_on_commit=False`; `session.refresh()` in a loop
  gets its own hint. The ini option `queryguard_column_load_threshold`
  (default 5 instances) and the `column_load_threshold` argument of
  `QueryLog.assert_no_nplusone()` set the limit. The kind works with
  `queryguard_ignore` and `allow_nplusone`.
- Write-loop detection, opt in: `queryguard_write_loop_threshold` (off by
  default) and the `write_loop_threshold` argument of
  `QueryLog.assert_no_nplusone()`. The same `INSERT`, `UPDATE` or `DELETE`
  shape run row by row from one source line is reported as the new finding kind
  `write-loop`, with a hint to use `session.add_all()`, `executemany` or a bulk
  statement. `executemany` and multi-row `VALUES` statements are not counted.
  The statements of one ORM flush count as one write, so `add_all()` and one
  `flush()` is not reported even where the dialect sends one `INSERT` per row;
  a `flush()` or `commit()` per row is. The flush is found through SQLAlchemy's
  `Session._flush` frame; if that changes, the rule counts every statement.
  The kind works with `queryguard_ignore`, `allow_nplusone` and
  `queryguard_fail_on`.
- Experimental detection of unused eager loads, off by default:
  `queryguard_unused_eager_load` (`off`, `warn` or `fail`). A relationship a
  test loads with `selectinload()`, `joinedload()`, `subqueryload()` or
  `immediateload()` and never reads is reported as the new finding kind
  `unused-eager-load` (label `Class.relationship`), with a hint to remove the
  option. While the option is `off` nothing is installed. Opt out with
  `allow_nplusone(kind="unused-eager-load")` or `queryguard_ignore`. See
  `docs/unused-eager-load.md` for how it works and its limits.
- `queryguard_small_repeat_threshold`, an ini option (off by default) and the
  `small_repeat_threshold` argument of `QueryLog.assert_no_nplusone()`: a
  statement that ran a few times (below `queryguard_repeat_threshold`) from one
  source line, each time with different parameters, is reported as a repeated
  statement. Identical parameters, runs from several lines, writes and
  pagination are left alone. The default threshold of 5 is unchanged, so a batch
  of two to four statements is not flagged unless this is turned on.

**Adopting it on an existing project**

- Baseline file for existing findings: `queryguard_baseline` (ini) or
  `--queryguard-baseline=PATH`, and `--queryguard-update-baseline` to write it.
  A finding whose identity (kind, label, file and function; rootdir-relative
  paths, no line numbers or counts) is in the file does not fail or warn, for
  every finding kind. New findings are reported as before, and entries that
  matched nothing in the run are listed as stale baseline entries. The file is
  JSON with a `version` field, sorted for clean diffs; a missing, corrupt or
  unknown-version file is a usage error. Works with pytest-xdist: workers apply
  the file, the controller merges what they saw and writes it on an update.
  See `docs/baseline.md`.
- SARIF output: `--queryguard-sarif=PATH` writes the findings of the run as
  SARIF 2.1.0 for GitHub code scanning (the `--queryguard-report` flag is
  unchanged). One rule per finding kind with a fix hint, one result per
  finding identity with a rootdir-relative location, `partialFingerprints`
  derived from the identity, and `error` or `warning` level following
  `queryguard_fail_on`. Ignored and baselined findings are left out. Works with
  pytest-xdist: workers send their findings to the controller, which writes the
  file once. See `docs/sarif.md`.
- A composite GitHub Action in `action/`: installs the plugin, runs pytest with
  `--queryguard-sarif`, uploads the file with `github/codeql-action/upload-sarif`
  (pinned by commit SHA) and fails the step when pytest fails. Inputs:
  `pytest-args`, `python-version`, `package`, `baseline`, `fail`,
  `working-directory`, `sarif-file`, `upload`, `category`. The upload is skipped
  for pull requests from forks. A CI job runs the action on
  `examples/action-sample`. See `docs/ci.md`.

**Ignoring and gating**

- `queryguard_ignore`, an ini option that leaves single findings out of the
  reports. One pattern per line, `KIND LABEL [file=GLOB] [function=GLOB]`,
  matched with `fnmatch`. The kinds are `relationship-load`,
  `repeated-statement`, `column-load`, `write-loop`, `unused-eager-load` and
  `missing-index`; `file` is relative to the rootdir.
- `allow_nplusone` takes label patterns and the keywords `kind`, `file` and
  `function` to ignore only the findings they match. Without arguments it
  still ignores the whole test.
- Ignore patterns that matched no finding in the run are listed in the
  terminal summary as stale ignores (not with pytest-xdist).
- `queryguard_fail_on`, an ini option with finding kinds (`fnmatch` patterns,
  one per line). In `fail` mode only these kinds fail a test; the other kinds
  are reported as `QueryGuardWarning`. Empty means every kind fails, as before.
  Budgets are not affected.

**Timing and budgets**

- Per-statement timing. `Query.duration` is the time in seconds the database
  and the driver took for a statement, measured around the cursor call;
  `QueryLog.db_time` is the total and `QueryLog.slowest(n)` the slowest
  statements.
- `max_db_time(seconds)`, a context manager, and the `max_db_time` marker:
  fail when the statements take longer than the limit in total. The failure
  lists the slowest statements. A `max_queries` failure now shows the total
  database time and its slowest statements as well.

**Documentation and examples**

- A documentation site built from `docs/` (`scripts/build_docs.py`, with the new
  `docs` dependency group: Markdown and Pygments): getting started, how detection
  works, a configuration reference that a test keeps in step with the options, FAQ,
  limitations, a comparison with other tools and a migration guide from nplusone.
  The README is now a short overview that links to it.
- `examples/` with runnable projects (sync N+1, async FastAPI, PostgreSQL and SQLite
  missing indexes, baseline and SARIF), each with a README and a smoke test in CI.

### Changed

**Behavior changes**

- The `column-load` finding kind is on by default (threshold 5, see
  `queryguard_column_load_threshold`). A test that loops over deferred columns, or
  over objects expired by `commit()`, passed before and now fails in `fail` mode.
  To keep such tests green while you fix them, use a baseline, `queryguard_fail_on`
  without `column-load` (it then only warns), or
  `queryguard_ignore = ["column-load *"]`.
- Those loops used to be reported as a `repeated-statement` with the wrong advice
  (`WHERE id IN (...)`); they are no longer counted by that rule, so a baseline or
  ignore entry for such a finding has to use the new kind.
- The index advisor is on by default whenever the index check is on
  (`queryguard_index_advisor = false` turns it off). Missing-index findings now carry
  a `fix: CREATE INDEX ...` line and an `alembic:` line, and without the hypopg
  extension one notice at the end of the session says how to install it
  (`queryguard_hypopg = off` silences it).
- `queryguard_index_check` now also checks SQLite. A project that turned the check
  on and ran its tests on SQLite used to get a "not checked" notice and now gets
  findings, which fail tests in `fail` mode.
- Durations below one millisecond are printed in microseconds (`37 us`) instead of
  `0.0 ms` in the report and in budget failures.
- The hint for a lazy-loaded many-to-one or one-to-one relationship is now
  `joinedload(...)`; collections still get `selectinload(...)`.
- A test stores at most 10000 statements and 10000 relationship loads for its
  reports (`queryguard_max_events`, `0` for no limit). Past the limit they are only
  counted.
- When a listener raises, the plugin still stops measuring so that it never breaks
  the suite, but it warns and prints a "queryguard: measurement stopped" section
  instead of staying silent (see Fixed).

Everything else is off by default and changes nothing until you turn it on:
`queryguard_small_repeat_threshold`, `queryguard_write_loop_threshold`,
`queryguard_unused_eager_load`, `queryguard_baseline`, `queryguard_fail_on` (empty:
every kind fails, as before) and `--queryguard-sarif`.

**Internals**

- The missing-index check reads the conditions of a plan node with a tokenizer
  and parser (`pytest_queryguard.predicates`) instead of regular expressions
  over the text. PostgreSQL's JSON plan has no structured form of a predicate,
  so the relation, alias, schema and index still come from the plan's fields and
  the condition text is parsed into a tree; the old text matching stays as the
  fallback for syntax the parser does not know. Findings do not change. The
  parsing is tested on real `EXPLAIN (GENERIC_PLAN, VERBOSE, FORMAT JSON)` plans
  captured from PostgreSQL 14 to 18 (`scripts/capture_plans.py`).
- The measurement registers one more SQLAlchemy listener (`after_cursor_execute`)
  to time statements. `scripts/bench_overhead.py` puts the added cost of all the
  measurement, not of this listener alone, at about 20 to 55 microseconds per
  statement on in-memory SQLite (roughly +30 to +40 %); against a real database
  round trip it is small. See the FAQ.
- Internal: detectors now hand their results to a stable `Finding` model
  (`kind`, `label`, `file`, `function`, `message`, `severity`) before the
  report is rendered. The messages and the public API are unchanged.
- The check that decides whether a stack frame belongs to SQLAlchemy, pytest or
  the standard library is now cached per file name. Measured with
  `scripts/bench_overhead.py`, this cuts the per-statement overhead of the
  measurement by about half.

### Fixed

- The index check no longer reports a missing index, and the advisor no longer
  recommends a redundant one, for a statement that an existing partial index
  may serve. The generic plan cannot see bound values, so it could not know
  that `status = $1` matches `... WHERE status = 'open'`. Such statements are
  skipped and counted in the session summary. A missing index on them is not
  found; see the limitations.
- Loops over deferred or expired columns were reported as a repeated statement
  with the wrong advice (`WHERE id IN (...)`). They are now a `column-load`
  finding and no longer count towards the repeated-statement rule.
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

[Unreleased]: https://github.com/MaciejZiel/pytest-queryguard/compare/v0.4.0rc2...HEAD
[0.4.0rc2]: https://github.com/MaciejZiel/pytest-queryguard/compare/v0.4.0rc1...v0.4.0rc2
[0.4.0rc1]: https://github.com/MaciejZiel/pytest-queryguard/compare/v0.3.0...v0.4.0rc1
[0.3.0]: https://github.com/MaciejZiel/pytest-queryguard/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/MaciejZiel/pytest-queryguard/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/MaciejZiel/pytest-queryguard/tree/v0.1.0
