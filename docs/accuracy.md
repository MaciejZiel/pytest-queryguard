# Accuracy on a corpus of realistic code

How often does pytest-queryguard report code that is fine, and how often does it
miss code that is not? This page measures that on a corpus of 138 small
scenarios, publishes the numbers, and lists every disagreement. The scenarios
were written by the author of the plugin, so read the numbers as a regression
baseline and a list of known limits, not as a benchmark of how the plugin does on
your code.

## Method

- The corpus is in `tests/corpus/scenarios`: one module per scenario, 63 marked
  **BUG** (an N+1 or a row-by-row write that a reviewer would ask to fix) and 75
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
2.0 CI job), so 136 of the 138 scenarios ran: 62 BUG and 74 CLEAN. All numbers on
this page are for those 136 and SQLite. The corpus was also run once on
PostgreSQL 17.11 (docker, pg8000 and asyncpg) to check that the rule for
pagination and chunks reads those drivers' parameters: every pagination and chunk
scenario agreed with its verdict there too. Two other scenarios fail on
PostgreSQL for an unrelated reason in the corpus seed
([#120](https://github.com/MaciejZiel/pytest-queryguard/issues/120)), so no
PostgreSQL numbers are published.

## Results, default configuration

54 of the 57 BUG scenarios that the default rules can report are flagged (the other
5 BUG scenarios are write loops and an unused eager load, which are opt-in), and 1 of
the 79 scenarios without an expected default kind is flagged.

| kind | TP | FP | FN | precision | recall |
|---|---|---|---|---|---|
| relationship-load | 19 | 0 | 0 | 100% | 100% |
| repeated-statement | 31 | 1 | 2 | 97% | 94% |
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

### Before and after the rule for pagination and chunks

On 2026-10-13 the repeat rules learned to leave pagination and chunked `IN`
loads alone when their parameters show them (see "Pagination and chunks" in
[How detection works](how-detection-works.md)). The same 136 scenarios, before
and after:

| configuration | repeated-statement before | after |
|---|---|---|
| default | 31 TP, 9 FP, 2 FN (78%, 94%) | 31 TP, 1 FP, 2 FN (97%, 94%) |
| small-repeat = 2 | 33 TP, 11 FP, 0 FN (75%, 100%) | 33 TP, 2 FP, 0 FN (94%, 100%) |
| repeat scope `session` | 27 TP, 8 FP, 6 FN (77%, 82%) | 27 TP, 1 FP, 6 FN (96%, 82%) |

No BUG scenario was lost in any configuration; the BUG scenarios that look like
pagination or batching (`bug_58` to `bug_60`, `bug_63`) are still reported.
Read the "before" column with care: five of the CLEAN scenarios
(`clean_62_dataloader_max_batch_size`, `clean_69_celery_batch_task_chunks`,
`clean_72_keyset_pagination_desc`, `clean_73_paginate_twice`,
`clean_74_chunked_in_string_keys`) were written together with the rule, to test
it, so they are false positives before it by construction. Without them, on the
131 scenarios measured before the change, the default row goes from 31 TP, 4 FP
(89%) to 31 TP, 1 FP (97%): the three false positives removed are
`clean_12_offset_pagination`, `clean_13_keyset_pagination` and
`clean_15_chunked_in_six_chunks`. `clean_14_chunked_in_three_chunks` is no longer
reported by the small-repeat rule either.

## Results with the opt-in rules

| configuration | kind | TP | FP | FN | precision | recall |
|---|---|---|---|---|---|---|
| small-repeat = 2 | repeated-statement | 33 | 2 | 0 | 94% | 100% |
| write-loop = 2 | write-loop | 7 | 2 | 0 | 78% | 100% |
| column-load threshold 2 | column-load | 5 | 1 | 0 | 83% | 100% |
| unused-eager-load = fail | unused-eager-load | 1 | 0 | 0 | 100% | 100% |
| repeat scope `session` | repeated-statement | 27 | 1 | 6 | 96% | 82% |
| all four rules | repeated-statement | 33 | 2 | 0 | 94% | 100% |
| all four rules | column-load | 5 | 1 | 0 | 83% | 100% |
| all four rules | write-loop | 7 | 2 | 0 | 78% | 100% |
| all four rules | unused-eager-load | 1 | 0 | 0 | 100% | 100% |

`relationship-load` stays at 19 TP, 0 FP, 0 FN in every configuration. The
unused-eager-load row is one BUG scenario and nothing else, so it says only that
the rule works on its textbook case. With all rules on, every BUG scenario is
flagged, and 5 of 74 CLEAN scenarios are.

**Effect of `queryguard_repeat_scope = session`** (added after the first run of this
corpus): the scenarios that run each lookup in its own `Session`
(`bug_47_session_per_item`, `bug_48_threadpool_lookups`,
`bug_52_async_gather_session_per_task`, `bug_53_celery_task_per_id`) are no longer
reported, which is what the option is for; they were reported before, and the four
false negatives are the price. In both scopes the only CLEAN scenario the default
rules report is the polling loop, which repeats one statement inside one `Session`.

## Every disagreement

| scenario | verdict | the plugin | why |
|---|---|---|---|
| `bug_42_small_get_loop_below_threshold`, `bug_45_async_session_get_small` | BUG: 4 and 3 `get` calls | not reported | The repeat threshold is 5. The small-repeat rule reports both. |
| `bug_44_expired_after_commit_three` | BUG: 3 objects reload after a commit | not reported | The column-load threshold is 5. Threshold 2 reports it. |
| `bug_50_cascade_delete_loop` | BUG: delete cascade loads per parent | `relationship-load` and also `repeated-statement` (before the fix) | Bug [#59](https://github.com/MaciejZiel/pytest-queryguard/issues/59), fixed: an autoflush before the lazy load took over its relationship attribution. After the fix the repeated-statement false positive of this scenario is gone, and the repeated-statement rows on this page were re-measured. |
| `clean_12_offset_pagination`, `clean_13_keyset_pagination`, `clean_15_chunked_in_six_chunks` | CLEAN: pages and chunks | `repeated-statement` before 2026-10-13, nothing after | Fixed by the rule for pagination and chunks ([#60](https://github.com/MaciejZiel/pytest-queryguard/issues/60)). `clean_14_chunked_in_three_chunks` was reported by the small-repeat rule before it. |
| `clean_16_polling_loop` | CLEAN: polling a status on purpose | `repeated-statement` | Every run has the same parameters, like the loop-invariant query of `bug_55_loop_invariant_query`, which is a bug. The statements cannot tell them apart. Use `allow_nplusone`. |
| `clean_20_cached_lookup` | CLEAN | `repeated-statement` with small-repeat on | Two statements of one shape from one line are what the rule reports. |
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
prefetching repository, `asyncio.gather` over different statements, and pagination
and chunked loads: offset and keyset pages (also newest first, and the same walk
twice), `IN` chunks of ids and of string keys, a DataLoader with a maximum batch
size, and Celery-style tasks per chunk.

## Release thresholds

A release does not go out when the corpus gets worse. The release gate
(`scripts/release_gate.py accuracy`, see "Releasing" in `CONTRIBUTING.md`) runs
the corpus with the default configuration and fails when the precision or the
recall of a finding kind is below the minimum recorded in
`scripts/accuracy_thresholds.json`, when fewer than 136 scenarios ran, or when a
disagreement with a verdict is not documented in the scenario. The minimums are
the values measured on 2026-10-13 (SQLAlchemy 2.1.1, pytest 9.1.1, SQLModel not
installed, 136 of the 138 scenarios), rounded down to a multiple of 10%:

| kind | minimum precision | minimum recall | measured |
|---|---|---|---|
| `relationship-load` | 100% | 100% | 19 TP, 0 FP, 0 FN |
| `repeated-statement` | 90% | 90% | 31 TP, 1 FP, 2 FN (97% and 94%) |
| `column-load` | 100% | 80% | 4 TP, 0 FP, 1 FN |

The repeated-statement minimum precision went from 80% to 90% with the rule for
pagination and chunks, in the same change. The minimums
are a floor against regressions, not a goal. Raise one in the same pull request
that improves the corpus result; lower one only with a reason in that pull
request, because every lowered minimum is a release that detects less.

## Comparison with queryspy and pytest-querycount

The same 136 scenarios, each tool with its defaults plus the switch that makes it
fail a test without a per-test marker (`--queryspy-strict`; the `no_n_plus_one` marker
added to every scenario for pytest-querycount). A scenario counts as "flagged" when
the test failed. Whether a tool reports the right kind is not compared.

Run on 2026-10-13, on the machine above, with queryspy 0.5.0 and pytest-querycount
0.4.0 installed in a scratch virtual environment (Python 3.13.13, SQLAlchemy 2.1.1,
pytest 9.1.1, the corpus's other dependencies at the versions above) and passed to
`scripts/accuracy_report.py --compare`.

| tool | BUG flagged | BUG missed | CLEAN passed | CLEAN flagged |
|---|---|---|---|---|
| pytest-queryguard, defaults | 54 | 8 | 73 | 1 |
| pytest-queryguard, all opt-in rules | 62 | 0 | 69 | 5 |
| queryspy 0.5.0 | 52 | 10 | 57 | 17 |
| pytest-querycount 0.4.0 | 57 | 5 | 57 | 17 |

- Of the BUG scenarios the defaults of pytest-queryguard miss, five are write loops
  or an unused eager load (opt-in here, not reported by the other two either) and
  three are below the thresholds, which the other tools flag.
- queryspy did not flag the Core `select()` loop, the `text()` loop, the driver-SQL
  loop and the f-string SQL loop, nor the thread-pool lookups; pytest-querycount
  flagged them. This agrees with the earlier comparison, where queryspy missed Core
  loops.
- Both other tools flagged seventeen CLEAN scenarios: the pagination, chunk, polling, cache and
  commit scenarios (pytest-queryguard reports the polling loop by default, and the
  cache and commit ones only with its opt-in rules on), a `session.refresh()` of a
  single object, a selectin-loaded tree, a `selectinload` over 3000 parents, a
  `selectinload` two levels deep over 1500 parents and `yield_per` with
  `selectinload`. A tool that treats every repeated shape as a finding will do
  that; the choice is between catching small loops and flagging batching.
- Five of those seventeen are the pagination and chunk scenarios written together
  with pytest-queryguard's rule for them (listed above), which makes this table
  favour pytest-queryguard even more than the rest of the corpus does.
- The scenarios were written by the author of pytest-queryguard, so the table
  favours it. Each of the other tools has
  options I did not tune (budgets, thresholds, ignore blocks).

## Known limits

See also [Limitations](limitations.md).

- Deliberate polling is reported by the default repeat rule, and so are the
  pagination and chunk shapes the rule for them does not read (composite keyset
  conditions, string or date keyset bounds, MySQL's `LIMIT offset, count`,
  `IN` lists shorter than 50 values, the same keys loaded twice).
- Below its thresholds a loop is not reported by default; the opt-in rules that
  lower them report legitimate code too (a cache, two objects after a commit).
- The write-loop rule cannot tell seeding or per-row inserts with error handling
  from a write loop.
- The corpus is small, written by one person, and SQLite only; precision and recall
  from 138 scenarios have wide error bars. PostgreSQL, MySQL and other drivers were
  not run.
- The SQLModel scenarios run only where SQLModel is installed.
