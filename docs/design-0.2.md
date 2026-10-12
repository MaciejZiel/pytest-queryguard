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
   reads a table (or a whole index) in full because no index matches its
   filter, even when the table has three rows.
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
copied unchanged, except that for psycopg and psycopg2 `%%` becomes `%` in
them too: those drivers replace it everywhere, while pg8000 leaves quoted
text alone. The `EXPLAIN` is executed without parameters, so drivers
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

Since 0.3, asyncpg engines are checked through a PL/pgSQL `DO` block on
the same connection; see [design-0.3.md](design-0.3.md). Other async
drivers keep the notice.

### 4.4 Which plans are reported

The plan tree is walked. A node is reported when it reads a whole table, or
a whole index, to apply a condition that an index could serve directly:

1. `Seq Scan` with a `Filter`: the `WHERE` condition has no index.
   Correlated subqueries and `EXISTS` are covered here, through the filter
   of the subquery's scan.
2. `Index Scan` or `Index Only Scan` with a `Filter` and no `Index Cond`,
   when the filter is an equality (`=` or `= ANY`) on one column: the
   planner walked a whole index (usually the primary key, for `ORDER BY`)
   just to avoid the disabled sequential scan. Without this rule
   `WHERE customer_id = $1 ORDER BY id LIMIT 10` would pass. Ranges and
   `IS NULL` (`WHERE total > 0 ORDER BY id LIMIT 20`) are left out: that is
   ordinary pagination, which walks the primary key on purpose. A
   `Bitmap Heap Scan` with such a filter and a `Bitmap Index Scan` without
   `Index Cond` directly below it is the same whole-index read; PostgreSQL
   picks it for an `INCLUDE` index before `ANALYZE` and an `Index Only Scan`
   after it, so both must be reported. The same holds for a partial index
   (`(id) WHERE deleted_at IS NULL`): before `ANALYZE` the heap scan's
   `Recheck Cond` is only the index predicate, after it the plan is an
   `Index Scan` with the filter. 0.2.0 required no `Recheck Cond` and missed
   the first plan (issue #4, fixed in 0.3). A `Bitmap Index Scan` below a
   `BitmapAnd` or `BitmapOr` is not judged this way: another index narrows
   the read, or the `OR` needs every arm.
3. `Index Scan`, `Index Only Scan` or `Bitmap Index Scan` whose
   `Index Cond` is an equality on one column and does not use the first key
   column of a btree index: with only `(tenant_id, status)`,
   `WHERE status = $1` reads the whole index on PostgreSQL 16 and 17. The
   first key column comes from `pg_index.indkey[0]` and `pg_attribute`,
   read inside the same savepoint, once per index per session (section 4.6), for btree
   indexes only (`pg_am.amname`): BRIN and GIN serve any of their columns.
   PostgreSQL 18 can skip-scan, so the rule and its lookup are not used
   there. If a catalog lookup fails, lookups stop for the session with one
   line in the summary, and rule 3 and the `::text` hint are not applied.

Not reported: a `Seq Scan` without a filter (`SELECT * FROM t`,
`count(*)`, a join without `WHERE`), because the statement reads the whole
table anyway; scans of `pg_catalog`, `information_schema` and `pg_toast`
(SQLAlchemy's reflection queries); other bitmap heap scans.

0.2 does not report join conditions on their own. A first version reported
a `Seq Scan` below a join on one of the table's columns. Review showed that
this depends on statistics: the same query on the same data planned as a
hash join with a sequential scan before `ANALYZE`, and as a nested loop
with `Memoize` after it. For a join without a filter, an index on the join
column does not change the production plan anyway.

### 4.5 Fix hint

`CREATE INDEX ON <table> (<column>)` is shown when the condition uses
exactly one column of the scanned table, compared with `=`, `<`, `<=`, `>`
or `>=` (`= ANY (...)` included), either directly or through `::text` on a
`varchar` column. The plan prints `(col)::text` for every cast to text, but
an index on the plain column serves it only for `varchar` (checked against
PostgreSQL 17: not for integers, enums, `char(n)` or `name`), so the check
reads the `varchar` columns of the tables involved from `pg_attribute` and
`pg_type`, inside the savepoint and once per table per session. Not inside a
function call such as `lower(...)`, and not through another cast
(`total::int`, `created_at::date`, `id::text` on an integer): an index on
the plain column does not serve those.
Then the message says `add an index that matches this condition (an
expression index may be needed)`. For other conditions it says `add an
index that matches this condition`, without guessing. The schema is shown
unless it is the connection's `current_schema()`, which the settings query
returns, so the hint works as written on that connection.

### 4.6 Deduplication and allowlists

- Per test, statements are explained once per engine and statement text,
  with whitespace and `IN` lists collapsed, so `IN` lists of any length
  count as one. Literals that SQLAlchemy writes into the text stay part of the
  key (a `NULL` or a constant in the SQL); values it binds do not exist in the
  statement text at all. DDL, and the end of a
  transaction that ran it, forget the statements explained so far, so the
  next identical statement is explained against the new catalog.
- A partial index cannot be judged by a generic plan. SQLAlchemy binds
  values, so `status = $1` carries no value and the planner cannot prove that
  `... (x) WHERE status = 'open'` serves it, although the custom plan of a
  real execution does use it (checked on PostgreSQL 16 with the statement
  `status = 'open' AND x = 10`). When the scanned table has a valid partial
  index whose predicate columns are all filtered by the statement, and at
  least one of them is compared with a parameter, the statement is not
  reported and gets no advice; it is counted and the session summary says so.
  A predicate the statement fixes with a literal (`deleted_at IS NULL`) is
  decided by the generic plan and still counts. A second `EXPLAIN` with the
  real values was considered and not built: the check sees the statement
  text, not the parameters, and binding them would be the unsound plan of
  `design-0.3.md` section 3.1.
- Per test, findings are grouped by table and column when the column is
  known (the same query under two aliases, as in a `UNION ALL`), otherwise
  by table and condition; each is reported once with the first statement
  and call site that produced it.
- Plans are not cached across tests: another test can have other tables,
  indexes, rows or a different `search_path`.
- Catalog lookups are cached for the session (since 0.3, issue #5; 0.2.0
  cached them per test). The cache holds, per engine, the first key column
  of each btree index by `(schema, index)` and the `varchar` columns of
  each table by `(schema, table)`; plans name both with their schema, so
  `search_path` does not matter. The engine is held weakly, so an engine
  created per test is looked up afresh. The whole cache, for every engine,
  is dropped:
  - when a statement seen in `after_cursor_execute`, on any engine, in a
    test or a fixture, may change the catalog. Anything that does not start
    with a keyword known to be safe (`SELECT`, `WITH`, `INSERT`, `UPDATE`,
    `DELETE`, `SET`, `BEGIN`, `COMMIT`, `ROLLBACK`, `SAVEPOINT`, `ANALYZE`,
    `VACUUM`, `LOCK`, `COPY` and a few more) counts, as does a second
    statement after a semicolon: `CREATE`, `DROP`, `ALTER`, `REINDEX`,
    `TRUNCATE`, `DO`, `CALL`, `DISCARD`, `EXPLAIN` and so on. Creating a
    new object alone could not make an entry wrong, since nothing is cached
    for a name that did not exist, but the rule does not try to tell;
  - when a transaction that ran such a statement commits, rolls back or
    rolls back to a savepoint (SQLAlchemy's `commit`, `rollback` and
    `rollback_savepoint` events, or the same statements as text). A
    rollback can bring back an index the transaction dropped, and a commit
    shows the change to the other connections, whose entries were read
    before it;
  - when such a connection is invalidated or garbage-collected with its
    transaction open, and when `COMMIT PREPARED` or `ROLLBACK PREPARED`
    runs;
  - at the start and end of every pytest session, so nested `pytester`
    sessions do not share it.

  DDL that SQLAlchemy does not run in this process is not seen: a raw
  DB-API connection, `psql`, another process (including other
  pytest-xdist workers on a shared database) or a function called from a
  `SELECT`. Such a change in the middle of a session can leave an index
  judged by its old definition; this is documented as a limitation.
  The check costs a few round trips per distinct statement, about 1 ms with
  pg8000 against a local PostgreSQL in Docker
  ([scripts/bench_index_check.py](https://github.com/MaciejZiel/pytest-queryguard/blob/main/scripts/bench_index_check.py)).
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
| Async engine other than asyncpg (since 0.3), or a paramstyle other than those in 4.2 | Skipped; one line in the session summary |
| `EXPLAIN` fails (a parameter type PostgreSQL cannot infer, a statement that cannot be planned generically) | Rolled back to the savepoint; counted, and the session summary gives the count and the first error |
| Not a `SELECT` or `WITH` (after leading comments), or `executemany` | Not checked |
| `SAVEPOINT` or `ROLLBACK TO SAVEPOINT` fails | The index check stops for the rest of the session, with one line in the summary; N+1 detection and budgets go on |

The summary lines are printed in the terminal summary, not as warnings, so
`filterwarnings = error` cannot turn a skipped check into a failure.
Under pytest-xdist the workers' summaries are not shown, so these lines are
not visible there.

### 4.8 Configuration and modes

| Option | Values | Default |
|---|---|---|
| `queryguard_index_check` (ini) | boolean | `false` |
| `--queryguard-index-check` (CLI) | flag | turns the check on for this run |
| `--no-queryguard-index-check` (CLI) | flag | turns the check off for this run; the last of the two flags wins |
| `queryguard_index_ignore_tables` (ini) | list of names or patterns | empty |

Findings follow `queryguard_mode` like N+1 findings: `fail` fails the test,
`warn` emits a `QueryGuardWarning`, `off` turns the check off with the rest
of detection. `allow_nplusone` does not affect the index check, and
`allow_missing_index` does not affect N+1 detection. When a test has a
budget failure, N+1 and index findings are added to the same failure.

### 4.9 Errors

An exception in the plugin's own code (not a database error from
`EXPLAIN`) is handled as in 0.1: it disables the plugin with one warning.
If the savepoint cannot be opened or `ROLLBACK TO SAVEPOINT` fails, the
plugin cannot vouch for the connection's state, so the index check stops
for the rest of the session and says so once in the session summary. N+1
detection and budgets go on; they do not touch the connection.

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
| False positives on small lookup tables and pagination | Equality-only rule for whole-index scans, no join rule, `queryguard_index_ignore_tables`, `allow_missing_index`, `warn` mode |
| Placeholder rewriting differs from a driver | Unit tests per paramstyle; a failed `EXPLAIN` is counted and reported, never fails the test |
| Slower suites | Off by default; one `EXPLAIN` per distinct statement per test |
| psycopg and psycopg2 are not tested in CI (license policy) | Checked by hand during review; the rewriting rules for their paramstyle are unit-tested |
