# pytest-queryguard 0.3: the missing-index check for async engines

| | |
|---|---|
| Status | Proposed |
| Author | Maciej Zieliński |
| Date | 2026-10-11 |
| Target release | 0.3.0 |
| Extends | [design-0.2.md](design-0.2.md), section 4.3 |

## 1. Problem

The 0.2 index check runs `EXPLAIN (GENERIC_PLAN, VERBOSE, FORMAT JSON)`
with the statement's `$1`, `$2`, ... placeholders through a raw DB-API
cursor on the test's own connection, inside a savepoint. Sync drivers send
a statement without parameters over the simple query protocol, where
PostgreSQL parses the `$n` as the generic plan's parameters.

asyncpg prepares every statement over the extended query protocol. There,
PostgreSQL counts the `$n` inside `EXPLAIN` as parameters of the `EXPLAIN`
itself, and asyncpg refuses to bind none:

```
InterfaceError: the server expects 1 argument for this query, 0 were passed
```

So 0.2 skips statements from async engines with a notice. Async
SQLAlchemy with asyncpg is common in FastAPI projects, which are the
projects most likely to use this plugin.

## 2. Goal

Check `SELECT` statements from `create_async_engine("postgresql+asyncpg://...")`
with the same rules, findings and guarantees as from sync engines:

- the plan is the generic plan, independent of the values the test used;
- the check runs on the test's own connection and transaction, so it sees
  tables, indexes and rows the test has not committed;
- the check leaves the test's transaction and settings as they were.

Non-goals: async drivers other than asyncpg (psycopg's async mode is LGPL
and cannot be tested here; it keeps the 0.2 notice), and PostgreSQL before
16.

## 3. Options

### 3.1 Bind values to the `EXPLAIN` (rejected: unsound)

asyncpg accepts the statement if it gets one value per placeholder. But
PostgreSQL then plans with those values even under `GENERIC_PLAN`. Checked
against PostgreSQL 17 with asyncpg 0.31:

- with `NULL` for `status = $1`, the plan is `Result` with
  `One-Time Filter: false`. Nothing is scanned, so nothing is reported;
- with the test's own value `'open'` and a partial index
  `WHERE status = 'open'`, the plan uses that index. That is the custom
  plan for this value, not the generic one that 0.2 judges.

Neither is the plan that every execution of the statement shares.

### 3.2 asyncpg's simple query path (not enough on its own)

`asyncpg.Connection.execute(query)` without arguments uses the simple
query protocol, so the generic `EXPLAIN` runs. But `execute()` returns
only the command tag (`"EXPLAIN"`). asyncpg has no public way to read rows
over the simple protocol; `fetch()` and friends always prepare.

### 3.3 A separate sync connection (rejected)

A second, sync connection to the same database could run the 0.2 code
unchanged. It does not fit:

- the plugin has no sync PostgreSQL driver to use. Runtime dependencies
  are pytest and SQLAlchemy only. pg8000 is a test dependency of this
  project, not of its users. psycopg and psycopg2 are LGPL, and many
  asyncpg projects have neither;
- it runs outside the test's transaction. It cannot see tables or indexes
  created in the test's still-open transaction, which is the usual layout
  of a suite (one transaction per test, rolled back at teardown).
  Statements on such tables would fail to plan. An index created in the
  test would be missed and give a false finding;
- it can block on locks the test's transaction holds, for example after
  `ALTER TABLE` in a fixture, until `lock_timeout` ends each check;
- it costs one more connection per engine. Its URL, credentials and SSL
  settings would have to be rebuilt from the async engine's.

Detecting these cases would make findings depend on the suite's layout.

### 3.4 `EXPLAIN` inside a `DO` block (chosen)

PL/pgSQL's `EXECUTE` parses its string on the server like the simple query
protocol: `EXPLAIN (GENERIC_PLAN ...)` with `$n` in that string plans
generically. A `DO` block has no parameters, so asyncpg's `execute()` can
send it over the simple protocol. The block stores the plan in a
transaction-local setting. A parameterless `SELECT` reads it back:

```sql
SAVEPOINT queryguard_explain;
SELECT set_config('enable_seqscan', 'off', true), ..., current_schema();
DO $qg_do$ DECLARE plan text; BEGIN
  EXECUTE $qg_sql$EXPLAIN (GENERIC_PLAN, VERBOSE, FORMAT JSON) <statement>$qg_sql$
  INTO plan;
  PERFORM set_config('queryguard.plan', plan, true);
END $qg_do$;
SELECT current_setting('queryguard.plan');
ROLLBACK TO SAVEPOINT queryguard_explain;
RELEASE SAVEPOINT queryguard_explain;
```

The statements go to the raw asyncpg connection through SQLAlchemy's
public `AdaptedConnection.run_async()`, which exists for calling the
driver from sync event handlers. Like 0.2's raw DB-API cursor, this
bypasses SQLAlchemy's events, so budgets, N+1 detection and user
listeners do not see the check. It also bypasses SQLAlchemy's own
prepared-statement cache. asyncpg's cache is another matter: it belongs to
the connection, holds 100 statements, and drops the least recently used
one first. Statements that return no rows (`SAVEPOINT`, `DO`, `ROLLBACK
TO SAVEPOINT`, ...) go through `execute()`: simple protocol, nothing
prepared. The `SELECT`s go through `fetch()`, which prepares them and
caches them in that cache. They are kept to a fixed set of four texts, so
the check takes at most four of the 100 slots however many statements it
explains: the settings read, the plan read, and two catalog lookups that
take the schema and relation names as `text[]` parameters instead of
literals. The check can still push the user's least recently used
prepared statements out of the cache, and each of them is then prepared
again on its next use. That costs one round trip, and only for a
connection whose cache is full and which the check touches.

Everything else is shared with the sync path. That covers the savepoint, or
`BEGIN` … `ROLLBACK` in autocommit mode, the settings, the catalog
lookups, error handling, the plan rules, the fix hint and the messages.

## 4. Decisions

- The dollar-quote tags are chosen so that they do not occur in the
  statement (`$qg_sql$`, or `$qg_sql_1$`, `$qg_sql_2$`, ...). The statement
  is then copied verbatim, with no escaping that could depend on
  `standard_conforming_strings`.
- Only asyncpg is supported among async drivers. Other async drivers keep
  a notice that names the driver.
- The asyncpg path is chosen from the dialect (`is_async` and driver
  `asyncpg`). SQLAlchemy 2.0, the oldest supported version, has
  `run_async()` on every adapted async connection.
- The setting name `queryguard.plan` is a custom placeholder setting. The
  rollback to the savepoint resets its value. PostgreSQL keeps knowing
  the placeholder for the rest of the connection: `current_setting(
  'queryguard.plan')` returns an empty string there instead of an error.
  This is documented.

## 5. Trade-offs and limits

| Trade-off | Consequence |
|---|---|
| Needs PL/pgSQL | Installed and usable by everyone by default. Without it, the first `EXPLAIN` fails with `language "plpgsql" does not exist`. The session summary then says once that asyncpg statements were not checked because PL/pgSQL is not installed, and no further asyncpg statement is explained. Sync engines are still checked. |
| One more round trip than the sync path | The `DO` and the `SELECT` replace one `EXPLAIN`. Measured against PostgreSQL 17 in local Docker, one check took 0.4–0.5 ms on asyncpg and 0.3–0.4 ms on pg8000. |
| Errors come from PL/pgSQL | The message is PostgreSQL's own (for example `could not determine data type of parameter $1`). The `CONTEXT` lines that PL/pgSQL adds are dropped, because only the first line is kept. |
| `queryguard.plan` stays known to the connection | Empty after the check; see section 4. |

## 6. Testing

- Unit tests for building the `DO` block, including statements that contain
  the default tags, quotes and `$$`.
- Against PostgreSQL 16 and 17 with asyncpg (Apache-2.0, with
  async-timeout, Apache-2.0, on Python 3.10), in CI:
  - the 0.2 scenarios on an async engine (missing index, existing index,
    uncommitted table, autocommit, failure to plan);
  - `AsyncSession` with lazy loads;
  - the state of the test's transaction and settings afterwards;
  - a comparison of the `DO` path's plans with plain `EXPLAIN` plans on the
    same connection for the same statements.
- The psycopg async driver still gets its notice; it is unit-tested with
  a fake dialect.
