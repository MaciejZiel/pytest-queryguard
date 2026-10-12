# Accuracy on a corpus of realistic code

How often does pytest-queryguard report code that is fine, and how often does it
miss code that is not? This page measures that on a corpus of 110 small
scenarios, publishes the numbers, and lists every disagreement. The scenarios
were written by the author of the plugin, so read the numbers as a regression
baseline and a list of known limits, not as a benchmark of how the plugin does on
your code.

## Method

- The corpus is in `tests/corpus/scenarios`: one module per scenario, 50 marked
  **BUG** (an N+1 or a row-by-row write that a reviewer would ask to fix) and 60
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
  and async versions (`AsyncSession`, `awaitable_attrs`, `run_sync`,
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

Measured on 2026-10-12 on Linux 7.0.0 (x86_64, 13th Gen Intel Core i5-13500H, 16
threads), Python 3.13.13, SQLAlchemy 2.1.1, pytest 9.1.1, SQLModel 0.0.47 (run with
`--no-deps`; it needs SQLAlchemy below 2.1 and is only installed in the
SQLAlchemy 2.0 CI job), Flask-SQLAlchemy 3.1.1, FastAPI 0.141.1, pytest-queryguard
0.4.0rc1 at the head of `main` including `queryguard_repeat_scope`. PostgreSQL was
not run: the numbers are SQLite only.

## Results, default configuration

43 of the 46 BUG scenarios that the default rules can report are flagged (the other
4 BUG scenarios are write loops and an unused eager load, which are opt-in), and 4 of
64 CLEAN scenarios are flagged.

| kind | TP | FP | FN | precision | recall |
|---|---|---|---|---|---|
| relationship-load | 20 | 0 | 0 | 100% | 100% |
| repeated-statement | 19 | 5 | 2 | 79% | 90% |
| column-load | 4 | 0 | 1 | 100% | 80% |

The small counts (20, 19, 4) mean that one scenario moves a percentage by several
points. The relationship-load row is 20 scenarios, not a claim about every ORM
pattern.

## Results with the opt-in rules

| configuration | kind | TP | FP | FN | precision | recall |
|---|---|---|---|---|---|---|
| small-repeat = 2 | repeated-statement | 21 | 7 | 0 | 75% | 100% |
| write-loop = 2 | write-loop | 6 | 2 | 0 | 75% | 100% |
| column-load threshold 2 | column-load | 5 | 1 | 0 | 83% | 100% |
| unused-eager-load = fail | unused-eager-load | 1 | 0 | 0 | 100% | 100% |
| repeat scope `session` | repeated-statement | 17 | 5 | 4 | 77% | 81% |
| all four rules | repeated-statement | 21 | 7 | 0 | 75% | 100% |
| all four rules | column-load | 5 | 1 | 0 | 83% | 100% |
| all four rules | write-loop | 6 | 2 | 0 | 75% | 100% |
| all four rules | unused-eager-load | 1 | 0 | 0 | 100% | 100% |

`relationship-load` stays at 20 TP, 0 FP, 0 FN in every configuration. The
unused-eager-load row is one BUG scenario and nothing else, so it says only that
the rule works on its textbook case. With all rules on, every BUG scenario is
flagged, and 9 of 60 CLEAN scenarios are.

**Effect of `queryguard_repeat_scope = session`** (added after the first run of this
corpus): the scenarios that run each lookup in its own `Session`
(`bug_47_session_per_item`, `bug_48_threadpool_lookups`) are no longer reported, which
is what the option is for; they were reported before, and the two false negatives
are the price. No false positive was removed, because every CLEAN scenario that is
reported repeats one statement inside one `Session`.

## Every disagreement

| scenario | verdict | the plugin | why |
|---|---|---|---|
| `bug_42_small_get_loop_below_threshold`, `bug_45_async_session_get_small` | BUG: 4 and 3 `get` calls | not reported | The repeat threshold is 5. The small-repeat rule reports both. |
| `bug_44_expired_after_commit_three` | BUG: 3 objects reload after a commit | not reported | The column-load threshold is 5. Threshold 2 reports it. |
| `bug_50_cascade_delete_loop` | BUG: delete cascade loads per parent | `relationship-load` and also `repeated-statement` (measured before the fix) | Bug [#59](https://github.com/MaciejZiel/pytest-queryguard/issues/59), fixed: an autoflush before the lazy load took over its relationship attribution. The numbers on this page were measured before the fix; after it, the repeated-statement false positive of this scenario is gone. |
| `clean_12_offset_pagination`, `clean_13_keyset_pagination` | CLEAN: 6 and 7 pages | `repeated-statement` | Five or more pages are one statement five times; only the small-repeat rule skips pagination. [#60](https://github.com/MaciejZiel/pytest-queryguard/issues/60). |
| `clean_15_chunked_in_six_chunks` | CLEAN: 3000 ids in chunks of 500 | `repeated-statement` | The chunks share one shape. |
| `clean_16_polling_loop` | CLEAN: polling a status on purpose | `repeated-statement` | A deliberate repeat looks like a loop. Use `allow_nplusone`. |
| `clean_14_chunked_in_three_chunks`, `clean_20_cached_lookup` | CLEAN | `repeated-statement` with small-repeat on | Two or three statements of one shape from one line are what the rule reports. |
| `clean_22_expire_on_commit_two_objects` | CLEAN | `column-load` with threshold 2 | Two reloads after a commit are the threshold. |
| `clean_18_factory_boy_seed_in_body`, `clean_30_savepoint_per_row_insert` | CLEAN: seeding and per-row inserts with their own error handling | `write-loop` with the rule on | The rule cannot tell them from a write loop. |
| `bug_47_session_per_item`, `bug_48_threadpool_lookups` | BUG | not reported with scope `session` | By design of the option. |

The three BUG scenarios below the default thresholds, and the BUG scenarios that
are only write loops or an unused eager load, are the "FN" column of the default
tables or are outside their scope; they are listed here rather than hidden.

Not shown because they agreed with the verdict: lazy-load loops through views,
serializers, Pydantic, FastAPI (sync and async), Flask-SQLAlchemy, SQLModel,
`__str__`, properties, hybrids, many-to-many, nested relationships, trees, Jinja
templates, `with_parent`, joined-table inheritance, deferred columns, expired
columns, `session.refresh`, Core and `text()` and driver SQL loops, `merge` loops,
dynamic relationships, threads and a session per item; and, as CLEAN, all the eager
loading fixes, loader options on the mapping, `raiseload`, identity-map hits, new
objects, backref assignment, executemany, multi-row `VALUES`, `add_all`, streaming,
savepoints, recursive CTEs and bulk updates.

## Comparison with queryspy and pytest-querycount

The same 110 scenarios, each tool with its defaults plus the switch that makes it
fail a test without a per-test marker (`--queryspy-strict`; the `no_n_plus_one` marker
added to every scenario for pytest-querycount). A scenario counts as "flagged" when
the test failed. Whether a tool reports the right kind is not compared.

| tool | BUG flagged | BUG missed | CLEAN passed | CLEAN flagged |
|---|---|---|---|---|
| pytest-queryguard, defaults | 43 | 7 | 56 | 4 |
| pytest-queryguard, all opt-in rules | 50 | 0 | 51 | 9 |
| queryspy 0.5.0 | 41 | 9 | 50 | 10 |
| pytest-querycount 0.4.0 | 46 | 4 | 50 | 10 |

- Of the BUG scenarios the defaults of pytest-queryguard miss, four are write loops
  or an unused eager load (opt-in here, not reported by the other two either) and
  three are below the thresholds, which the other tools flag.
- queryspy did not flag the Core `select()` loop, the `text()` loop, the driver-SQL
  loop and the f-string SQL loop, nor the thread-pool lookups; pytest-querycount
  flagged them. This agrees with the earlier comparison, where queryspy missed Core
  loops.
- Both other tools flagged ten CLEAN scenarios: the pagination, chunk, polling, cache and
  commit scenarios (pytest-queryguard reports some of them only with its opt-in rules on), a `session.refresh()` of a
  single object, a selectin-loaded tree and a `selectinload` over 3000 parents. A
  tool that treats every repeated shape as a finding will do that; the choice is
  between catching small loops and flagging batching.
- The scenarios were written by the author of pytest-queryguard, so the table
  favours it. Each of the other tools has
  options I did not tune (budgets, thresholds, ignore blocks).

## Known limits

See also [Limitations](limitations.md).

- Pagination of five or more pages, chunked `IN` loads and deliberate polling are
  reported by the default repeat rule.
- Below its thresholds a loop is not reported by default; the opt-in rules that
  lower them report legitimate code too (a cache, two objects after a commit).
- The write-loop rule cannot tell seeding or per-row inserts with error handling
  from a write loop.
- The corpus is small, written by one person, and SQLite only; precision and recall
  from 110 scenarios have wide error bars. PostgreSQL, MySQL and other drivers were
  not run.
- The SQLModel scenarios run only where SQLModel is installed.
