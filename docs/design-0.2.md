# pytest-queryguard 0.2 — missing index detection

| | |
|---|---|
| Status | Accepted |
| Author | Maciej Zieliński |
| Date | 2026-10-11 |
| Target release | 0.2.0 |
| Extends | [design.md](design.md), section 3 |

## 1. Problem

Test databases are tiny, so a query that reads a whole table is as fast in
CI as one that uses an index. The missing index shows up only in
production, when the table has grown. The N+1 checks of 0.1 do not see
this: the query runs once and looks fine.

## 2. Goals

1. Opt-in: with the check on, fail (or warn about) a test whose `SELECT`
   reads a table in full because no index matches its filter or join
   condition, even when the table has three rows.
2. Name the table, the condition from the plan, the SQL and the line that
   ran it, and suggest `CREATE INDEX ON orders (customer_id)` when the
   column is obvious.
3. Cost nothing when the check is off, and never break the user's suite or
   the test's own transaction.

Non-goals for 0.2: async engines, PostgreSQL before 16, other databases,
judging whether an existing index is a good one, and `INSERT`, `UPDATE` or
`DELETE` statements.

## 3. Core idea

For each distinct `SELECT` the test body runs, PostgreSQL plans it with
sequential scans disabled:

```sql
SAVEPOINT queryguard_explain;
SELECT set_config('enable_seqscan', 'off', true),
       set_config('lock_timeout', '1s', true),
       set_config('statement_timeout', '5s', true),
       current_schema();
EXPLAIN (GENERIC_PLAN, VERBOSE, FORMAT JSON) <statement with $1, $2, ...>;
ROLLBACK TO SAVEPOINT queryguard_explain;
RELEASE SAVEPOINT queryguard_explain;
```

`enable_seqscan = off` does not forbid a sequential scan; it makes it so
expensive that the planner takes any index path that exists. A sequential
scan that survives therefore means no index can serve that access path, on
any table size. `GENERIC_PLAN` (PostgreSQL 16+) plans the statement without
parameter values, so one plan stands for every execution of the statement,
whatever values the test used. `VERBOSE` adds the schema of each table and
qualifies every column in the conditions with its table alias, which the
fix hint needs.

## 4. Decisions

### 4.1 Which connection runs EXPLAIN

The test's own connection, right after the statement ran
(`after_cursor_execute`), inside a savepoint.

- A second connection cannot see tables or data the test created in its
  still-open transaction, which is the usual layout of a test suite (one
  transaction per test, rolled back at teardown). It can also block on the
  locks that transaction holds, and with `StaticPool` it is not a second
  connection at all.
- On the same connection, `ROLLBACK TO SAVEPOINT` undoes everything the
  check did: the `set_config(..., true)` settings and, if `EXPLAIN` fails,
  the error that would otherwise abort the test's transaction. Cursors
  opened before the savepoint stay open.
- The statements go through a new raw DB-API cursor. SQLAlchemy events do
  not fire for them, so they are not counted in budgets or N+1 checks and
  the user's own listeners do not see them. The cursor that holds the
  test's results is not touched.
- When the connection is in autocommit mode (the DB-API `autocommit`
  attribute is true), there is no transaction to protect, so the check runs
  in its own `BEGIN` … `ROLLBACK` instead of a savepoint.
- `lock_timeout` and `statement_timeout` bound the time the check can wait.

### 4.2 Statement text

`GENERIC_PLAN` needs `$1`-style placeholders, and drivers send other
styles. The listener sees the text in the dialect's paramstyle and
rewrites it:

- `format` (pg8000): `%s` becomes `$n`, `%%` becomes `%`.
- `pyformat` (psycopg2, psycopg): `%(name)s` becomes `$n`, the same name
  getting the same number; `%s` and `%%` as above.
- `numeric_dollar`: unchanged.
- Anything else: not checked, reported once per session (section 4.7).

Quoted strings, quoted identifiers, dollar-quoted strings and comments are
copied unchanged. The `EXPLAIN` is executed without parameters, so drivers
send it as-is over the simple query protocol. This matters: over the
extended protocol PostgreSQL counts the `$n` inside `EXPLAIN` as parameters
of the `EXPLAIN` itself and rejects a bind with none. pg8000 and psycopg2
use the simple protocol for a statement without parameters. Passing the
original values instead would let PostgreSQL fold them into the plan, which
defeats `GENERIC_PLAN`.

### 4.3 Sync and async engines

Sync engines only in 0.2. The async drivers (asyncpg, psycopg's async
mode) prepare every statement over the extended protocol, which rejects
`$n` inside `EXPLAIN` (section 4.2). Statements from an async engine are
skipped with one notice per session.

### 4.4 Which plans are reported

The plan tree is walked. A node is reported when it reads a whole table
although the statement narrows it down:

- `Seq Scan` with a `Filter`: the `WHERE` condition has no index.
- `Index Scan` or `Index Only Scan` with a `Filter` and no `Index Cond`:
  the planner walked a whole index (usually the primary key, for `ORDER BY`)
  just to avoid the disabled sequential scan; the filter itself has no
  index. Without this rule `WHERE customer_id = $1 ORDER BY id LIMIT 10`
  would pass.
- `Seq Scan` without a filter below a join whose condition (`Hash Cond`,
  `Merge Cond` or `Join Filter`) uses a column of that table: the join
  column has no index.

Not reported: a `Seq Scan` with no filter and no join, such as
`SELECT * FROM t` or `SELECT count(*) FROM t`, because reading the whole
table is what the statement asks for; scans of `pg_catalog`,
`information_schema` and `pg_toast` (SQLAlchemy's reflection queries); and
bitmap scans, which already use an index.

A full join of two whole tables is reported when its join column has no
index. That is deliberate: with the index in place the planner avoids the
scan, and an unindexed foreign key is also what slows down deletes on the
parent table. Tables where this is fine are allowlisted (section 4.6).

### 4.5 Fix hint

`CREATE INDEX ON <table> (<column>)` is shown when the condition uses
exactly one column of the scanned table, directly or through a cast
(`(orders.status)::text`), compared with `=`, `<`, `<=`, `>` or `>=`
(`= ANY (...)` included), and not inside a function call such as
`lower(...)`. Otherwise the message says to add an index that matches the
condition, without guessing. The schema is shown unless it is the
connection's `current_schema()`, which the settings query returns, so the
hint works as written on that connection.

### 4.6 Deduplication and allowlists

- Per test, statements are explained once per engine and statement text,
  with whitespace and `IN` lists collapsed, so `IN` lists of any length
  count as one. Literals are kept: a partial index can serve
  `status = 'open'` and not `status = 'closed'`.
- Per test, findings are grouped by table and condition; each is reported
  once with the first statement and call site that produced it.
- There is no cache across tests: another test can have other tables,
  indexes or a different `search_path`, and an `EXPLAIN` costs about a
  millisecond.
- `queryguard_index_ignore_tables` lists table names or `fnmatch` patterns
  (`countries`, `audit.*`, `lookup_*`) matched against `table` and
  `schema.table`. Small lookup tables, where a sequential scan is the right
  plan in production too, go here.
- `@pytest.mark.allow_missing_index` turns the check off for one test;
  `@pytest.mark.allow_missing_index("countries")` ignores only those
  tables in that test.

### 4.7 Statements and servers that cannot be checked

| Case | Behaviour |
|---|---|
| Engine is not PostgreSQL | Skipped; one line in the session summary |
| PostgreSQL before 16 (`server_version_info`, no query) | Skipped; one line in the session summary with the version |
| Async engine, or a paramstyle other than those in 4.2 | Skipped; one line in the session summary |
| `EXPLAIN` fails (a parameter type PostgreSQL cannot infer, a statement that cannot be planned generically) | Rolled back to the savepoint; counted, and the session summary gives the count and the first error |
| Not a `SELECT` or `WITH`, or `executemany` | Not checked |

The summary lines are printed in the terminal summary, not as warnings, so
`filterwarnings = error` cannot turn a skipped check into a failure.
Under pytest-xdist the workers' summaries are not shown, so these lines are
not visible there.

### 4.8 Configuration and modes

| Option | Values | Default |
|---|---|---|
| `queryguard_index_check` (ini) | boolean | `false` |
| `--queryguard-index-check` (CLI) | flag | turns the check on for this run |
| `queryguard_index_ignore_tables` (ini) | list of names or patterns | empty |

Findings follow `queryguard_mode` like N+1 findings: `fail` fails the test,
`warn` emits a `QueryGuardWarning`, `off` turns the check off with the rest
of detection. `allow_nplusone` does not affect the index check, and
`allow_missing_index` does not affect N+1 detection. When a test has a
budget failure, N+1 and index findings are added to the same failure.

### 4.9 Errors

An exception in the plugin's own code (not a database error from
`EXPLAIN`) is handled as in 0.1: it disables the plugin with one warning.
If even `ROLLBACK TO SAVEPOINT` fails, the plugin cannot vouch for the
connection's state, so it records an internal error and stops checking.

## 5. Message

```
queryguard: missing index

  orders is read in full: Seq Scan with filter (orders.customer_id = $1)
  first at app/orders.py:12 in orders_for
  sql: SELECT orders.id, orders.customer_id FROM orders WHERE orders.customer_id = %s::INTEGER
  fix: CREATE INDEX ON orders (customer_id)

  a sequential scan is fine on a small lookup table:
  list it in queryguard_index_ignore_tables
```

## 6. Testing

- Unit tests with no database: placeholder rewriting, plan walking, the
  fix hint, messages, and the runner against a fake DB-API connection
  (savepoint, autocommit and error paths).
- Integration tests against a real PostgreSQL through pg8000 (BSD-3-Clause;
  psycopg is LGPL and stays out of the project), enabled by
  `QUERYGUARD_PG_URL`. Without the variable they are skipped and the
  default suite needs no database.
- CI: a job with a PostgreSQL service for versions 15 (the "too old"
  notice), 16 and 17.

## 7. Risks

| Risk | Mitigation |
|---|---|
| The check disturbs the test's transaction | Savepoint per check, raw cursor, settings local to the savepoint; internal error if the rollback fails |
| False positives on small lookup tables and full joins | `queryguard_index_ignore_tables`, `allow_missing_index`, `warn` mode |
| Placeholder rewriting differs from a driver | Unit tests per paramstyle; a failed `EXPLAIN` is counted and reported, never fails the test |
| Slower suites | Off by default; one `EXPLAIN` per distinct statement per test |
| psycopg and psycopg2 are not tested in CI (license policy) | Documented; the rewriting rules for their paramstyle are unit-tested |
