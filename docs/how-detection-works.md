# How detection works

pytest-queryguard does not read your code. It listens to the SQL that
SQLAlchemy sends while a test function runs, and applies a few rules to what
it saw.

## What is measured

1. When pytest starts (unless `queryguard_mode = off`; budgets still install
   the listeners when a test needs them), the plugin adds class-level
   SQLAlchemy listeners: `do_orm_execute` on `Session` sees relationship lazy
   loads, and `before_cursor_execute` and `after_cursor_execute` on `Engine`
   see every SQL statement and time it.
2. Measurement covers only the test function body, so queries in fixture
   setup and teardown are not counted. unittest `setUp` and `tearDown` run
   inside the test call and are counted.
3. Each statement is stored with its call site: the first stack frame outside
   SQLAlchemy, pytest, the standard library, `site-packages` and the plugin
   itself. For async sessions the search continues in the parent greenlets,
   where the awaiting code runs.
4. After the test, the stored events are checked against the budget and the
   rules below. A test stores at most `queryguard_max_events` statements;
   past that they are only counted, and the report says so.

The findings are then filtered by `queryguard_ignore`, the
`allow_nplusone` marker and the [baseline](baseline.md), and reported as a
failure, a `QueryGuardWarning` or a line in the [SARIF file](sarif.md),
depending on `queryguard_mode` and `queryguard_fail_on`.

## The rules

Every rule produces a finding with a kind, a label, a call site and a hint.

### relationship-load

One relationship (for example `Order.customer`) is lazy-loaded for at least
`queryguard_relationship_threshold` (default 2) different parent objects.
The label is the relationship. The hint is `joinedload` for a many-to-one or
one-to-one relationship and `selectinload` for a collection. The rule counts
distinct parent objects, not statements, so it is independent of how
SQLAlchemy batches the loads.

### repeated-statement

One `SELECT` runs at least `queryguard_repeat_threshold` (default 5) times in
one test. Statements are compared after normalization: whitespace is
collapsed, literals and parameter placeholders become `?`, and `IN (...)`
lists of any length are one shape. Statements already reported as a
relationship or column load are not counted again, and `INSERT`, `UPDATE`
and `DELETE` are never counted by this rule. Pagination and chunked `IN`
loads are not counted either, when their parameters show it (see below). The
label is the normalized statement. This rule sees Core and `text()`
statements as well as ORM ones.

With `queryguard_small_repeat_threshold` set (off by default, for example
`2`), a statement that ran at least that many times but fewer than the repeat
threshold is reported too, if all of these hold: every run came from the same
source line, every run had different parameters, the statement is not a write,
and it is not pagination (`OFFSET`, or `LIMIT` with a `>` or `<` condition on
a parameter) or a chunked `IN` load as described below.

#### Pagination and chunks

Walking through a result page by page, and loading a long list of keys in
slices, run one statement shape many times on purpose. Both rules leave such
runs alone when the statements and their bound parameters show it. Each
parameter is classified by the SQL just before it: after `OFFSET` it is the
offset, after `LIMIT` (or `FETCH FIRST`, `TOP`) the page size, after `<`,
`<=`, `>` or `>=` a range bound, inside an `IN (...)` list a list member, and
anything else a plain value. Literal `LIMIT 4 OFFSET 8` values count as page
size and offset. The values come from the parameters the driver got, by
position (`?`, `%s`, `$1`) or by name (`%(name)s`, `:name`).

The runs of one shape are **pagination** when all of these hold:

- the statement has `OFFSET`, or `LIMIT` with a range bound;
- the plain values, the `IN` lists and the page size are the same in every run:
  only the cursor (the offsets and range bounds) changes. A loop over items
  changes a plain value (`WHERE customer_id = ?`), so the first page of each
  customer's orders is still reported;
- the page size, when it is known, is more than one row. `LIMIT 1 OFFSET n`
  reads a table one row per statement and is still reported, which answers the
  concern in [#60](https://github.com/MaciejZiel/pytest-queryguard/issues/60);
- the cursor moves the same way (up or down) from run to run and never repeats,
  and an offset moves by at least the page size, so pages do not overlap. The
  walk may start again from its first cursor (a test that reads the same list
  twice); a restart anywhere else is reported.

The runs are a **chunked `IN` load** when all of these hold:

- one `IN` list changes from run to run and everything else is the same;
- the lists share no value: together they partition one list of keys;
- every list has the same length, at least 10 values, except one shorter list
  (the remainder, in any position).

The minimum of 10 is there because per-item lists are short: the two
addresses of an order, the lines of one order. A list of one id per run is
reported.

What these rules cannot see, and so still report: a polling loop and a
loop-invariant query, which both repeat one statement with the same
parameters (the first is fine, the second is a bug, and the statements are the
same); composite keyset conditions such as `a < ? OR (a = ? AND b < ?)`, where
the bound is also a plain value; MySQL's `LIMIT offset, count`; `IN` lists
written as literals; and two chunked loads of the same keys in one test (the
lists overlap). A parameter that cannot be read (an unknown placeholder
style, values that do not compare) means the statements are reported as
before.

### column-load

At least `queryguard_column_load_threshold` (default 5) instances of one class
each go back to the database for columns they already have a row for. There
are two causes, and the report tells them apart: a deferred column
(`deferred()`, `defer()`, `load_only()`), where the hint is
`undefer(Model.column)`; and attributes expired by `commit()`, `expire()` or
`expire_all()`, where the hint is `expire_on_commit=False` (a `refresh()` in a
loop gets its own hint). The label is the class and its deferred columns
(`Book.summary`) or the class and the cause (`Book (expired)`).

### write-loop

Off by default. With `queryguard_write_loop_threshold` set (for example `5`),
the same `INSERT`, `UPDATE` or `DELETE` shape, one row per statement, run that
many times from one source line is reported, with a hint to write the rows
together (`session.add_all()` and one flush, or
`session.execute(insert(Model), rows)`). `executemany` and multi-row `VALUES`
statements are already batched and never counted. The statements of one ORM
flush count as one: on a dialect without `insertmanyvalues`, `session.add_all()`
and one `flush()` still send one `INSERT` per row, and that is not a loop in
your code. A `flush()` or `commit()` per row, which is, is reported. The flush
is recognized from the call stack (SQLAlchemy's `Session._flush`); if a future
SQLAlchemy release changes that, such a flush is reported again until the
detection is updated.

### What the count rules count together

The relationship, repeated-statement, column-load and write-loop rules count
over a scope. By default (`queryguard_repeat_scope = test`) the scope is the
whole test. With `session`, each SQLAlchemy `Session`, or each connection
checkout for statements no `Session` ran, is counted on its own, and a finding
needs one scope to reach the threshold. See [Tests with several sessions or
requests](several-sessions.md).

### unused-eager-load (experimental)

Off by default. See [Unused eager loads](unused-eager-load.md).

### missing-index

Off by default; PostgreSQL 16 and newer, and SQLite. See the
[PostgreSQL](index-check.md) and [SQLite](index-check-sqlite.md) pages.

## Thresholds and why

| Option | Default | Why this value |
|---|---|---|
| `queryguard_relationship_threshold` | 2 | One lazy load of a relationship is normal. Two parents each loading it is the smallest case of a loop over rows, and the rule has to work on test databases with a few rows. Raise it if two is too strict for your code (see the [FAQ](faq.md)). |
| `queryguard_repeat_threshold` | 5 | A few runs of one statement are often a deliberate small batch, so the default is higher than for relationships. |
| `queryguard_small_repeat_threshold` | 0 (off) | Catches loops of two to four runs; off because it can report small legitimate repetition, so it is a choice. |
| `queryguard_column_load_threshold` | 5 | Touching a few objects after a `commit()` is ordinary test code; the limit is as high as the repeat rule's. |
| `queryguard_write_loop_threshold` | 0 (off) | Row-by-row writes are normal in many tests, so the kind is opt-in. |

The defaults are choices, not measurements. Every threshold is an ini option.

## What the rules cannot see

See [Limitations](limitations.md). In short: only SQLAlchemy statements are
seen, only inside the test body, and a rule says that a pattern happened, not
that production would be slow.
