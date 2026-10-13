# Limitations


- SQLAlchemy only. The plugin listens to SQLAlchemy's events, so a statement
  that does not go through SQLAlchemy is not counted, not budgeted and not
  checked for indexes, and nothing says so. That covers a raw `sqlite3`,
  `psycopg` or `asyncpg` connection used directly, and the Django ORM, Peewee,
  Tortoise ORM and any other ORM. SQLModel and Flask-SQLAlchemy build on
  SQLAlchemy sessions, so their statements should be seen (not tested here). If your suite passes but you expect findings, run
  with `--queryguard-report`: a test that shows 0 statements was not seen. For
  Django see the [comparison](comparison.md).
- The repeat rules count per test by default, so a test that makes several
  independent requests can report a repeat that is no loop. Set
  `queryguard_repeat_scope = session` to count per `Session`; that has limits
  of its own, listed in [Tests with several sessions or
  requests](several-sessions.md).
- Everything in `site-packages` counts as library code. If your application
  is installed non-editable (for example `pip install .` in a Docker CI
  image), its frames are skipped too, and call sites point to the test file
  instead of the application line.
- One measurement is active per process at a time, so tests in one process
  must run sequentially. Under pytest-xdist each worker measures its own
  tests, so detection and budgets work. The report needs a single process,
  so under pytest-xdist it prints
  `queryguard report: not available with pytest-xdist`.
- Tests that use `pytester` to run pytest in-process are also measured by the
  outer test, which sees the inner run's queries. Mark such tests with
  `allow_nplusone` or use `pytester.runpytest_subprocess()`.
- unittest `setUp` and `tearDown` run inside the test call, so their queries
  are counted, unlike pytest fixtures.
- A non-strict `xfail` test that has an N+1 is reported as xfailed rather
  than XPASS, because the N+1 failure looks like the expected failure.
- `INSERT`, `UPDATE` and `DELETE` never count towards the repeated-statement
  rule: at cursor level a unit-of-work flush cannot be told apart from an
  application write. Row-by-row writes from one source line can be reported
  by the opt-in `write-loop` kind (`queryguard_write_loop_threshold`), and a
  budget counts every statement, writes included. The statements of one ORM
  flush count as one write, so `add_all()` plus one `flush()` is not reported
  even when the dialect sends one `INSERT` per row. That relies on finding
  `Session._flush` on the call stack, a SQLAlchemy internal.
- The repeat rules leave pagination and chunked `IN` loads alone only when the
  bound parameters show them (see "Pagination and chunks" in
  [How detection works](how-detection-works.md)). Composite keyset conditions,
  keyset walks on a string or date column, MySQL's `LIMIT offset, count`,
  literal `IN` lists, `IN` lists shorter than 50 values and the same keys
  loaded twice in one test are still reported. A loop that sends 50 or more
  keys per item, each item's own and the same number for every item, looks
  like chunks and is not reported. Neither
  rule can tell a polling loop that repeats a statement on purpose from a
  loop-invariant query inside a loop: both repeat one statement with the same
  parameters, so both are reported. Raise `queryguard_repeat_threshold` or mark
  the test with `allow_nplusone` for such code. The
  [accuracy page](accuracy.md) lists the scenarios.
- A few statements below the thresholds are not reported by default: fewer than
  five runs of one statement (`queryguard_small_repeat_threshold` reports them)
  and fewer than five instances that reload columns after a commit
  (`queryguard_column_load_threshold`). The same thresholds make the opt-in
  rules report legitimate code: a cached lookup that runs twice, or two objects
  read after a commit.
- The write-loop rule cannot tell test seeding (Factory Boy creating rows with
  a flush each) or a per-row insert with its own error handling from a write
  loop in the code under test. It is off by default for that reason.
- The PostgreSQL index check needs PostgreSQL 16 or newer, and a sync engine or an
  async engine on asyncpg. Other async drivers, such as psycopg's async
  mode, are skipped with a notice. The test suite uses pg8000 and asyncpg.
  psycopg2 and psycopg 3 were checked by hand during review and give the
  same findings for plain statements, `%` in SQL, expanding `IN`,
  autocommit and `stream_results`; they are LGPL-licensed, so this project
  does not depend on them or run them in CI.
- On asyncpg the check needs PL/pgSQL, which PostgreSQL installs by
  default. Without it, each statement is counted as one PostgreSQL could
  not plan. After the first check, the connection knows the custom setting
  `queryguard.plan`, with an empty value: `current_setting('queryguard.plan')`
  returns `''` there instead of an error.
- The index check looks only at `SELECT` and `WITH` statements and judges
  plans, not data: it says that no index can serve a filter, not that an
  existing index is a good one. A missing index that only a join would use
  (a join without a `WHERE` on that table) is not reported, because the
  plan for it depends on statistics.
- The index check plans with a generic plan, which cannot see bound values.
  When a table has a partial index (`... WHERE status = 'open'`) whose
  predicate columns are all filtered by the statement, and one of them is
  compared with a parameter, the plan cannot tell whether the index serves
  that value. Such a statement is not reported and gets no advice; the
  session summary counts them (`N statements skipped: a partial index on
  <table> may serve them; the generic plan cannot tell`). A genuinely
  missing index on such a statement is therefore not found.
- A partitioned table is reported under the partition's name, and
  `queryguard_index_ignore_tables` matches partition names, not the parent
  table's.
- The index check clears its catalog cache on DDL it sees through
  SQLAlchemy in the same process. DDL run another way while the session
  runs, through a raw DB-API connection, `psql`, another process or a
  function called from a `SELECT`, is not seen, and the check may judge an
  index by its old definition. A fresh pytest session starts with an empty
  cache.
- The index check's session summary is printed by the process that runs the
  tests, so under pytest-xdist it is not shown. Findings in tests are
  reported as usual.
- On pytest 9.0 and 9.1, a wrongly typed value in the native `[tool.pytest]`
  table for one of pytest's own options (for example
  `console_output_style = 3`) stops pytest with an internal error, with or
  without this plugin. Wrong types for this plugin's options are reported
  as usage errors.

