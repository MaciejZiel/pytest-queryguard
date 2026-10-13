# Accuracy on a corpus of realistic code

How often does pytest-queryguard report code that is fine, and how often does it
miss code that is not? This page measures that on a corpus of 133 small
scenarios, publishes the numbers, and lists every disagreement. The scenarios
were written by the author of the plugin, so read the numbers as a regression
baseline and a list of known limits, not as a benchmark of how the plugin does on
your code.

## Method

- The corpus is in `tests/corpus/scenarios`: one module per scenario, 63 marked
  **BUG** (an N+1 or a row-by-row write that a reviewer would ask to fix) and 70
  marked **CLEAN** (code that is fine, including the fixes: `selectinload`,
  `joinedload`, `contains_eager`, bulk statements, pagination, caches). The
  docstring of each module says which, and why. The verdict is mine, from reading
  the code; for a BUG the module also names the finding kinds a perfect tool
  would report.
- The code imitates what people write: list views, hand-written serializers,
  Pydantic `from_attributes` models, FastAPI endpoints under `TestClient`,
  Flask-SQLAlchemy views, SQLModel relationships, repository classes, `__str__`
  and properties that hide a query, hybrid properties, many-to-many tables,
  joined-table inheritance, trees, `with_parent`, deferred columns, commits that
  expire objects, `get_or_create` loops, `session.merge`, savepoints, bulk
  inserts, pagination, `IN` chunks of 500, polling loops, Factory Boy seeding,
  GraphQL-style field resolvers and DataLoaders, `asyncio.gather` fan-out,
  Celery-style tasks run eagerly, caching repositories, `yield_per` with and
  without `selectinload`, `with_for_update`, nested transactions, upserts
  (`ON CONFLICT DO UPDATE`), ORM bulk `INSERT` and `UPDATE` with a list of
  rows, and async versions (`AsyncSession`, `awaitable_attrs`, `run_sync`,
  `stream_scalars`). Each scenario seeds a SQLite file in a setup step that is
  not measured; only `run()` is.
- Every scenario runs as a test of a real pytest session with the plugin
  installed, once per configuration: the defaults, then each opt-in rule on its
  own (`queryguard_small_repeat_threshold = 2`, `queryguard_write_loop_threshold = 2`,
  `queryguard_column_load_threshold = 2`, `queryguard_unused_eager_load = fail`),
  then `queryguard_repeat_scope = session`, then all opt-in rules together.
  The findings of each test are read back from the plugin.
- A scenario is a true positive for a kind when the kind is in its expected set
  and the plugin reports it; a false positive when the plugin reports a kind that
  is not expected; a false negative when an expected kind is not reported.
  Kinds are counted only in the configurations that can report them (the
  write-loop kind in the configurations that turn it on, and so on).
- The same scenarios ran under queryspy 0.5.0 and pytest-querycount 0.4.0 in a
  separate virtual environment (not a dependency of this project). They report
  pass or fail per test, not a finding kind, so that comparison is by scenario.
- In the regular test suite `tests/corpus/test_corpus.py` turns each
  (scenario, configuration) pair into a test. A disagreement that is a documented
  limit is a strict `xfail` with its reason, so a change in the plugin's
  behaviour shows up as a failing test and the entry must be removed.
  `scripts/accuracy_report.py` prints the tables below.

Measured on 2026-10-13 on Linux 7.0.0 (x86_64, 13th Gen Intel Core i5-13500H, 16
threads), Python 3.13.13, SQLAlchemy 2.1.1, pytest 9.1.1, Flask-SQLAlchemy 3.1.1,
FastAPI 0.141.1, pytest-queryguard 0.4.0rc2 at the head of `main`, with
`uv run python scripts/accuracy_report.py --compare <python>`. SQLModel was not
installed (it needs SQLAlchemy below 2.1 and is only installed in the SQLAlchemy
2.0 CI job), so 131 of the 133 scenarios ran: 62 BUG and 69 CLEAN. All numbers on
this page are for those 131. These numbers are from SQLite; the same corpus on
PostgreSQL 17 is in [On PostgreSQL](#on-postgresql).

## Results, default configuration

54 of the 57 BUG scenarios that the default rules can report are flagged (the other
5 BUG scenarios are write loops and an unused eager load, which are opt-in), and 4 of
the 74 scenarios without an expected default kind are flagged.

| kind | TP | FP | FN | precision | recall |
|---|---|---|---|---|---|
| relationship-load | 19 | 0 | 0 | 100% | 100% |
| repeated-statement | 31 | 4 | 2 | 89% | 94% |
| column-load | 4 | 0 | 1 | 100% | 80% |

The small counts (19, 31, 4) mean that one scenario moves a percentage by several
points. The relationship-load row is 19 scenarios, not a claim about every ORM
pattern.

The repeated-statement precision went from 83% to 89% when 13 BUG and 10 CLEAN
scenarios were added on 2026-10-13, with the plugin unchanged: the same 4 false
positives against 12 more true positives. That is a change of the corpus, not of
the plugin. The 12 new repeated-statement BUG scenarios include shapes chosen to
look like batching or pagination (`LIMIT 1 OFFSET n` per row, the first page of
each customer's orders, `IN` lists of one id or built from each item) and a
loop-invariant query with the same parameters on every run.

## Results with the opt-in rules

| configuration | kind | TP | FP | FN | precision | recall |
|---|---|---|---|---|---|---|
| small-repeat = 2 | repeated-statement | 33 | 6 | 0 | 85% | 100% |
| write-loop = 2 | write-loop | 7 | 2 | 0 | 78% | 100% |
| column-load threshold 2 | column-load | 5 | 1 | 0 | 83% | 100% |
| unused-eager-load = fail | unused-eager-load | 1 | 0 | 0 | 100% | 100% |
| repeat scope `session` | repeated-statement | 27 | 4 | 6 | 87% | 82% |
| all four rules | repeated-statement | 33 | 6 | 0 | 85% | 100% |
| all four rules | column-load | 5 | 1 | 0 | 83% | 100% |
| all four rules | write-loop | 7 | 2 | 0 | 78% | 100% |
| all four rules | unused-eager-load | 1 | 0 | 0 | 100% | 100% |

`relationship-load` stays at 19 TP, 0 FP, 0 FN in every configuration. The
unused-eager-load row is one BUG scenario and nothing else, so it says only that
the rule works on its textbook case. With all rules on, every BUG scenario is
flagged, and 9 of 69 CLEAN scenarios are.

**Effect of `queryguard_repeat_scope = session`** (added after the first run of this
corpus): the scenarios that run each lookup in its own `Session`
(`bug_47_session_per_item`, `bug_48_threadpool_lookups`,
`bug_52_async_gather_session_per_task`, `bug_53_celery_task_per_id`) are no longer
reported, which is what the option is for; they were reported before, and the four
false negatives are the price. No false positive was removed, because every CLEAN scenario that is
reported repeats one statement inside one `Session`.

## Every disagreement

| scenario | verdict | the plugin | why |
|---|---|---|---|
| `bug_42_small_get_loop_below_threshold`, `bug_45_async_session_get_small` | BUG: 4 and 3 `get` calls | not reported | The repeat threshold is 5. The small-repeat rule reports both. |
| `bug_44_expired_after_commit_three` | BUG: 3 objects reload after a commit | not reported | The column-load threshold is 5. Threshold 2 reports it. |
| `bug_50_cascade_delete_loop` | BUG: delete cascade loads per parent | `relationship-load` and also `repeated-statement` (before the fix) | Bug [#59](https://github.com/MaciejZiel/pytest-queryguard/issues/59), fixed: an autoflush before the lazy load took over its relationship attribution. After the fix the repeated-statement false positive of this scenario is gone, and the repeated-statement rows on this page were re-measured. |
| `clean_12_offset_pagination`, `clean_13_keyset_pagination` | CLEAN: 6 and 7 pages | `repeated-statement` | Five or more pages are one statement five times; only the small-repeat rule skips pagination. [#60](https://github.com/MaciejZiel/pytest-queryguard/issues/60). |
| `clean_15_chunked_in_six_chunks` | CLEAN: 3000 ids in chunks of 500 | `repeated-statement` | The chunks share one shape. |
| `clean_16_polling_loop` | CLEAN: polling a status on purpose | `repeated-statement` | A deliberate repeat looks like a loop. Use `allow_nplusone`. |
| `clean_14_chunked_in_three_chunks`, `clean_20_cached_lookup` | CLEAN | `repeated-statement` with small-repeat on | Two or three statements of one shape from one line are what the rule reports. |
| `clean_22_expire_on_commit_two_objects` | CLEAN | `column-load` with threshold 2 | Two reloads after a commit are the threshold. |
| `clean_18_factory_boy_seed_in_body`, `clean_30_savepoint_per_row_insert` | CLEAN: seeding and per-row inserts with their own error handling | `write-loop` with the rule on | The rule cannot tell them from a write loop. |
| `bug_47_session_per_item`, `bug_48_threadpool_lookups`, `bug_52_async_gather_session_per_task`, `bug_53_celery_task_per_id` | BUG | not reported with scope `session` | By design of the option. |

The three BUG scenarios below the default thresholds, and the BUG scenarios that
are only write loops or an unused eager load, are the "FN" column of the default
tables or are outside their scope; they are listed here rather than hidden.

Not shown because they agreed with the verdict: lazy-load loops through views,
serializers, Pydantic, FastAPI (sync and async), Flask-SQLAlchemy, SQLModel,
`__str__`, properties, hybrids, many-to-many, nested relationships, trees, Jinja
templates, `with_parent`, joined-table inheritance, deferred columns, expired
columns, `session.refresh`, Core and `text()` and driver SQL loops, `merge` loops,
dynamic relationships, threads and a session per item, GraphQL-style resolvers, a
DataLoader awaited key by key, `asyncio.gather` and Celery-style fan-out, caching
repositories that miss, `with_for_update` per row, `yield_per` with a query per row,
`LIMIT 1 OFFSET n` walks, a first page per customer, `IN` lists of one id or built
per item, a loop-invariant query and per-row upserts (with the write-loop rule on);
and, as CLEAN, all the eager loading fixes, loader options on the mapping,
`raiseload`, identity-map hits, new objects, backref assignment, executemany,
multi-row `VALUES`, `add_all`, streaming, savepoints, recursive CTEs, bulk updates,
a batching DataLoader, `selectinload` two levels deep over 1500 parents, `yield_per`
with `selectinload`, ORM bulk `UPDATE` and `INSERT` with a list of rows, a multi-row
upsert, one `FOR UPDATE` for a batch, a nested transaction around a batch, a
prefetching repository and `asyncio.gather` over different statements.

## Release thresholds

A release does not go out when the corpus gets worse. The release gate
(`scripts/release_gate.py accuracy`, see "Releasing" in `CONTRIBUTING.md`) runs
the corpus with the default configuration and fails when the precision or the
recall of a finding kind is below the minimum recorded in
`scripts/accuracy_thresholds.json`, when fewer than 131 scenarios ran, or when a
disagreement with a verdict is not documented in the scenario. The minimums are
the values measured on `main` on 2026-10-12 (SQLAlchemy 2.1.1, pytest 9.1.1,
SQLModel not installed), rounded down to a multiple of 10%; the scenario count
and the measured column are from 2026-10-13 (131 of the 133 scenarios):

| kind | minimum precision | minimum recall | measured |
|---|---|---|---|
| `relationship-load` | 100% | 100% | 19 TP, 0 FP, 0 FN |
| `repeated-statement` | 80% | 90% | 31 TP, 4 FP, 2 FN (89% and 94%) |
| `column-load` | 100% | 80% | 4 TP, 0 FP, 1 FN |

The repeated-statement minimums were not raised when the 2026-10-13 scenarios
pushed the measured precision from 83% to 89%: the plugin did not change, and
rounded down the new values give the same minimums. The minimums
are a floor against regressions, not a goal. Raise one in the same pull request
that improves the corpus result; lower one only with a reason in that pull
request, because every lowered minimum is a release that detects less.

## Comparison with queryspy and pytest-querycount

The same 131 scenarios, each tool with its defaults plus the switch that makes it
fail a test without a per-test marker (`--queryspy-strict`; the `no_n_plus_one` marker
added to every scenario for pytest-querycount). A scenario counts as "flagged" when
the test failed. Whether a tool reports the right kind is not compared.

Run on 2026-10-13, on the machine above, with queryspy 0.5.0 and pytest-querycount
0.4.0 installed in a scratch virtual environment (Python 3.13.13, SQLAlchemy 2.1.1,
pytest 9.1.1, the corpus's other dependencies at the versions above) and passed to
`scripts/accuracy_report.py --compare`.

| tool | BUG flagged | BUG missed | CLEAN passed | CLEAN flagged |
|---|---|---|---|---|
| pytest-queryguard, defaults | 54 | 8 | 65 | 4 |
| pytest-queryguard, all opt-in rules | 62 | 0 | 60 | 9 |
| queryspy 0.5.0 | 52 | 10 | 57 | 12 |
| pytest-querycount 0.4.0 | 57 | 5 | 57 | 12 |

- Of the BUG scenarios the defaults of pytest-queryguard miss, five are write loops
  or an unused eager load (opt-in here, not reported by the other two either) and
  three are below the thresholds, which the other tools flag.
- queryspy did not flag the Core `select()` loop, the `text()` loop, the driver-SQL
  loop and the f-string SQL loop, nor the thread-pool lookups; pytest-querycount
  flagged them. This agrees with the earlier comparison, where queryspy missed Core
  loops.
- Both other tools flagged twelve CLEAN scenarios: the pagination, chunk, polling, cache and
  commit scenarios (pytest-queryguard reports some of them only with its opt-in rules on), a `session.refresh()` of a
  single object, a selectin-loaded tree, a `selectinload` over 3000 parents, a
  `selectinload` two levels deep over 1500 parents and `yield_per` with
  `selectinload`. A tool that treats every repeated shape as a finding will do
  that; the choice is between catching small loops and flagging batching.
- The scenarios were written by the author of pytest-queryguard, so the table
  favours it. Each of the other tools has
  options I did not tune (budgets, thresholds, ignore blocks).

## On PostgreSQL

The same 131 scenarios, every configuration, with the database on PostgreSQL
instead of an SQLite file.

- Method: `QUERYGUARD_CORPUS_PG_URL=postgresql+pg8000://.../qg_corpus uv run
  python scripts/accuracy_report.py`. Each scenario starts from an empty
  `public` schema; sync scenarios use pg8000 and async ones asyncpg (the same
  URL with `+asyncpg`). The index check is off, as in the SQLite run: this
  page covers the N+1, repeat, column-load, write-loop and unused-eager-load
  rules. The run uses one database, so it cannot be split over several
  `pytest-xdist` workers; the regular `tests/corpus` tests also ran there
  (`pytest -n 0 tests/corpus`, 993 passed, 57 xfailed as documented limits).
- Server: one PostgreSQL 17.11 in the official `postgres:17` Docker image on
  the machine of the SQLite run (Linux 7.0.0, x86_64, 13th Gen Intel Core
  i5-13500H, 16 threads). Other PostgreSQL versions were not run.
- Software: Python 3.13.13, SQLAlchemy 2.1.1, pytest 9.1.1,
  pg8000 1.31.5, asyncpg 0.31.0, Flask-SQLAlchemy 3.1.1, FastAPI 0.141.1,
  SQLModel not installed (131 of 133 scenarios ran), pytest-queryguard
  0.4.0rc2 at `main`, run on 2026-10-13.

**Result: the output of `scripts/accuracy_report.py` on PostgreSQL 17 is
identical to the output on SQLite** (the SQLite run was on Python 3.14.4, the
same commit and packages): the same TP, FP and FN in every table and the same disagreements,
scenario by scenario. With the default configuration, 54 of 57 BUG scenarios
that its rules can report are flagged and 4 of 74 CLEAN scenarios are; with
all opt-in rules, 60 of 62 and 9 of 69. The report took 2 minutes 20 seconds on PostgreSQL.

**What the first PostgreSQL run found.** Before the fix, two BUG scenarios
(`bug_23_get_or_create_loop`, `bug_30_flush_per_insert`) were listed as
`UNEXPLAINED` without a finding: their first `INSERT` raised `duplicate key
value violates unique constraint "tag_pkey"`. The seed data inserts explicit
primary keys; SQLite numbers the next row after the largest one, but a
PostgreSQL sequence still starts at 1. That was a bug of the corpus
([#120](https://github.com/MaciejZiel/pytest-queryguard/issues/120)), not of
the plugin: the scenario never reached its loop. The corpus now moves every
serial and identity sequence past the seeded rows after a scenario's setup
(`Ctx.sync_sequences()`). The run showed no difference between PostgreSQL and
SQLite that comes from the plugin.

## Known limits

See also [Limitations](limitations.md).

- Pagination of five or more pages, chunked `IN` loads and deliberate polling are
  reported by the default repeat rule.
- Below its thresholds a loop is not reported by default; the opt-in rules that
  lower them report legitimate code too (a cache, two objects after a commit).
- The write-loop rule cannot tell seeding or per-row inserts with error handling
  from a write loop.
- The corpus is small and written by one person; precision and recall from 133
  scenarios have wide error bars. It ran on SQLite and on PostgreSQL 17 (pg8000
  and asyncpg); other PostgreSQL versions, MySQL and other drivers were not run.
- The SQLModel scenarios run only where SQLModel is installed.
