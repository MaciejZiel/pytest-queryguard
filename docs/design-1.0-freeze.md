# Design: the API freeze before 1.0

The last audit of the public surface before 1.0.0rc1 (planned for the end of
November 2026). From 1.0 on, everything that [Stability](stability.md) lists
as public follows semver: a rename needs a deprecation and a removal needs a
major release, and a changed default needs a major release too. This page
lists what we would regret freezing, ranked by importance, with what to do
before rc1.

It was written against `main` at 0.9.0, with #357 (stabilization batch 5,
which promotes the in-application mode out of experimental) still open, and
with the Django backend (#353, branch `feat/django`) in mind. An earlier
audit, #319 (0.8.0), wrote the naming conventions and the snapshot in
`tests/test_public_api.py`; this one checks the surface against them.

## What was checked

- The 24 ini options and 15 command-line flags, with their names, values
  and defaults (`plugin.py`, [Configuration](configuration.md), `llms.txt`).
- The 7 markers and the `queryguard` fixture.
- `pytest_queryguard.__all__` (14 names) with signatures, and
  `pytest_queryguard.app.__all__`.
- The 8 finding kinds, their labels and the SARIF rule ids.
- The baseline, budget, `--queryguard-json`, SARIF, trend, static-check and
  panel files, their format numbers and how each reader treats keys it does
  not know.
- The exceptions and warning classes.
- `python -m pytest_queryguard.compare`, `trend`, `lint` and `mcp`, the MCP
  tool names and results, and the GitHub Action's inputs and outputs.
- The promises in [Stability](stability.md), [Python API](python-api.md),
  [Configuration](configuration.md), [Baseline](baseline.md), [CI](ci.md)
  and `llms.txt`.

## In short

- **One real defect:** the baseline reader rejected keys it did not know.
  The stability page promises that new keys may be added in a minor release.
  With that reader, a baseline written by 1.1 with one more key would stop
  every 1.0 run that reads it. It is fixed in this change.
- **One-way doors.** After 1.0, a check that is off by default can only be
  turned on in 2.0. `write-loop` and the small-repeat rule should stay off
  (73% and 78% to 96% precision at threshold 2 on the corpus). That decision
  should be written down now.
- **#357 freezes the in-application mode.** Two things should change before
  it does, or in it: `app.Finding` is the internal `Finding` dataclass, and
  `watch()`, `unwatch()` and `is_watched()` name their argument `engine`.
- **Nothing needs a rename with a deprecation.** Every naming wart found
  either follows a known convention (the ini option and the marker
  `queryguard_max_queries` share a name, as pytest-timeout's `timeout` does),
  can be fixed later by adding something, or would cost more churn than it
  saves. The table says which applies to each one.
- **The deprecated names stay until 2.0.** They were deprecated in 0.8.0
  (2026-11-06). The policy keeps a deprecated name for at least three
  months, so none can go before 2027-02-06, which is after 1.0. After 1.0 a
  removal needs a major release, so "Removed in 2.0" is the only release the
  policy allows.

## The items, by importance

"This PR" says what this change does about the item: **fixed**, **doc**
(documentation only), or **—** (left as a recommendation).

| # | Item | Current state | Problem | Recommendation | Cost to users | This PR |
|---|---|---|---|---|---|---|
| 1 | Baseline reader and unknown keys | `baseline.parse()` stopped with `findings[i] has unknown keys` when an entry had any key besides `kind`, `label`, `file` and `function`. | [Stability](stability.md#files-tools-and-the-action) says new keys may be added in a minor release. With this reader, a `reason` or `count` key added in 1.x would make a baseline written by 1.x unreadable by 1.0, for example for a teammate or a CI job pinned to an older release, or for the action with `package` set. The format could then only grow with `version: 2`. | Keep `version: 1`. Ignore unknown keys in entries; the top level already did. An update writes only the four identity keys. Keep rejecting a key that looks like a misspelled identity key (`fucntion`), which would otherwise match nothing. A key that would narrow what an entry matches (one test only) changes the identity and needs `version: 2`. | None. A file that failed before now loads. A hand-added `line` key, which never narrowed the match, is now ignored instead of rejected. | **fixed** (`baseline.py`, `tests/test_baseline.py`, a reader test in `tests/test_public_api.py`, [Baseline](baseline.md#what-an-entry-is)) |
| 2 | Off-by-default stable checks | `queryguard_write_loop_threshold = 0` and `queryguard_small_repeat_threshold = 0` (off). The kinds are not experimental. | From 1.0 on, "a change of default needs a major release". Turning either on by default after rc1 means waiting for 2.0. | Keep both off, and decide that on purpose before rc1. On the corpus at threshold 2, write-loop has 73% precision, because seeding and per-row writes are often deliberate, and small-repeat has 78% to 96%. If one should be on in 1.0, use the pre-1.0 clause in rc1, not later. | None if kept. | — (decision for the maintainer) |
| 3 | `app.Finding` (in-app) | `pytest_queryguard.app.Finding` is the internal `findings.Finding`. Besides `kind`, `label`, `file`, `function`, `line`, `count` and `message`, it carries `severity`, `tests`, `peak`, `advice` (`IndexAdvice`, not exported), `table`, `stack`, `fix` (`SourceFix`, not exported) and the `identity` property. | #357 makes everything in `app.__all__` public, together with its signature. Neither the snapshot nor the docs say which fields of `Finding` are covered. Two of them are typed with classes that are not exported. Any refactoring of the plugin's internal dataclass would then be a breaking change. | In #357, or right after it and before rc1: say in [Stability](stability.md) that the public fields of `app.Finding` are `kind`, `label`, `file`, `function`, `line`, `count` and `message`, that the others are internal, and that it is not constructible by users. Snapshot those fields. A separate slim class is the cleaner option, but it is a larger change. | None (documentation). | — (belongs in #357) |
| 4 | `app.watch(engine)`, `unwatch(engine)`, `is_watched(engine)`, panel key `watched_engines` | The parameter is positional-or-keyword, so `watch(engine=e)` works and the name `engine` is frozen. | `engine` is a SQLAlchemy term. A Django in-app mode (named as later work in #353) would watch connections, or nothing at all. #357 freezes the name. | Before #357 merges: make the parameter positional-only (`watch(engine, /)`), so that a later release can accept other objects without a rename. Keep the `watched_engines` key and document it as "the watched SQLAlchemy engines"; a neutral key can be added later. | Breaks `watch(engine=...)` with a keyword, which no doc shows. Allowed while the mode is experimental. | — (belongs in #357) |
| 5 | Labels made from SQL text | The labels of `repeated-statement` and `write-loop` are normalized SQL. Stability promised that "an existing ignore keeps matching the same finding". | The text comes from the ORM. SQLAlchemy 2.0 and 2.1 render aliases differently, which [Baseline](baseline.md#what-an-entry-is) and `llms.txt` already note. The general promise read as if queryguard guaranteed the text itself. | Keep. Say that the normalization is covered and the ORM's text is not. Django's SQL differs from SQLAlchemy's, so this matters more once #353 lands. | None. | **doc** ([Stability](stability.md#what-is-public)) |
| 6 | Deprecated names | `queryguard_repeat_scope`, `repeat_scope=` (`assert_no_nplusone()`, `app.Settings`) and `max_queries(n=...)`, deprecated since 0.8.0 and removed in 2.0. | Removing them in rc1 would break policy rule 3 ("at least three months"), because 0.8.0 is from 2026-11-06. `app.Settings.repeat_scope` could legally go now while the mode is experimental, but the docs already promise 2.0 in three places. When `n=` goes in 2.0, the plugin must not fail pytest-querycount's own `max_queries(n=...)` markers. Today it reads `n=` silently when querycount is installed. | Keep all three until 2.0. When 2.0 removes `n=`, leave an unprefixed `max_queries(n=...)` to pytest-querycount when that plugin is installed, instead of failing the test. | None now. | — (2.0 note) |
| 7 | `queryguard_scope = session` | Values `test` and `session`. `session` means each SQLAlchemy `Session` or connection checkout; #353 maps it to each test-client request for Django. | The word reads like pytest's `session` scope, which is the broadest scope, while here it is narrower than `test`. It is also a SQLAlchemy word. | Keep `session`. Renaming a value of a public option costs every user who set it. #357 already words it as "database session". A neutral alias, such as `unit`, can be added in any 1.x minor, because a new accepted value is an addition. | None. | — |
| 8 | `ignore()` against the `queryguard_ignore` marker | `ignore("relationship-load")` takes kinds as positional arguments; `@pytest.mark.queryguard_ignore("Order.customer", kind=...)` takes labels. | The same verb takes different positional arguments. `@pytest.mark.queryguard_ignore("relationship-load")` is a label pattern that matches nothing. | Keep both, because changing either breaks every use. In 1.x, add a warning when a label pattern of the marker is exactly a kind name ("did you mean kind=...?"). | None. | — |
| 9 | Budget file values | `{"format": 1, "budgets": {nodeid: int}}`, and the reader requires an integer. | A per-test value cannot grow into an object (a time budget, a margin) without `format: 2`. | Keep it. New per-test data goes into a new top-level map (for example `"db_time": {nodeid: seconds}`), which the 1.0 reader already ignores. Written down as the rule that a value keeps its type. | None. | **doc** ([Stability](stability.md#files-tools-and-the-action)) |
| 10 | `message` in `--queryguard-json` findings | The key is written and snapshotted. [CI](ci.md#query-counts-as-json) says its wording is not stable. The stability table listed it neither as public nor as not public. | It was unclear whether a program may rely on it. | The key is public, and its text is not. | None. | **doc** |
| 11 | `statements`: a number or a list | In `--queryguard-json`, the trend and MCP results, `statements` is a count. In the panel JSON and `RequestReport`, it is the list of statements, and the count is `statement_count`. | One key has two types across public formats. | Keep it. #357 documents the choice, and changing it would need `format: 2` of `--queryguard-json`. Use `statement_count` in any new format that carries both. | None. | — |
| 12 | `QueryBudgetExceeded` and `QueryGuardWarning` docstrings, `llms.txt` | The docstrings said "more statements than its budget" and "N+1 or a missing index in warn mode". `llms.txt` said "the plugin's two rules" and left `ignore()` and `QueryGuardDeprecationWarning` out of the public list. | `QueryBudgetExceeded` is also raised for the time budget and for `num_queries()` with too few statements. `QueryGuardWarning` is used for every finding that warns, for example `cartesian-load` by default and kinds outside `queryguard_fail_on`. `assert_no_nplusone()` has three rules plus two optional ones. | Fix the texts. The names fit: "budget" covers time, and `QueryGuardWarning` was never only about N+1. | None. | **fixed** |
| 13 | `max_queries_marker` in `--queryguard-json` | A boolean: the test has a `max_queries` or `queryguard_max_queries` marker. | It does not show the limit or a budget that came from the budget file or `queryguard_max_queries`. | Keep it. Add a `budget` key (limit and source) in a minor release if the compare report needs it. | None. | — |
| 14 | `queryguard_fail_on` and the per-check `fail` values | A kind fails when `queryguard_mode = fail`, its own option (if any) is `fail`, and `queryguard_fail_on` is empty or matches it. | There are two knobs for one decision, and an empty `fail_on` does not mean "nothing". | Keep. [Configuration](configuration.md) states the rule exactly. Merging the knobs would change the meaning of existing configurations. | None. | — |
| 15 | Three ways to silence a missing index | `queryguard_index_ignore_tables` and `allow_missing_index` skip the check (no `EXPLAIN`), while `queryguard_ignore` with `kind=missing-index` filters the report. | It overlaps, but the cost differs: skipping saves round trips. | Keep all three. | None. | — |
| 16 | `queryguard_ignore` without arguments | Leaves out every finding except `missing-index`, which needs `allow_missing_index`. | The marker's `--markers` text says "N+1 query findings", and the configuration page says "all of them". Both are near the truth but not equal. | Keep the behaviour, because widening it would hide index findings in existing suites. The help text is not public, and its wording can be fixed at any time. | None. | — |
| 17 | `allow_missing_index` | The only marker without a `queryguard_` spelling. The prefixed spellings exist for names that other plugins read, such as pytest-querycount's `max_queries`. | No known plugin reads `allow_missing_index`. | Keep it. Add `queryguard_allow_missing_index` only if a clash appears; that is an addition. | None. | — |
| 18 | `queryguard_hypopg` | It is the only index option without `index_` (`queryguard_index_check`, `queryguard_index_advisor`, `queryguard_index_ignore_tables`). | The grouping is inconsistent. | Keep it. A rename would warn every PostgreSQL user who set it, for a cosmetic gain. | None. | — |
| 19 | `queryguard_index_check` is a bool | The strength of other checks is `off`, `warn` or `fail`; the index check follows `queryguard_mode` and `queryguard_fail_on`. | It looks inconsistent with the convention. | Keep it. If `warn` or `fail` is ever wanted, accept them next to `true` and `false`, which is an addition. | None. | — |
| 20 | `--queryguard-report` | A flag with no value that prints 10 tests. | It can never take a value: `nargs="?"` would swallow `--queryguard-report tests/`. | Keep it. A number of rows would be a new option. | None. | — |
| 21 | `queryguard.max_queries()` and `max_queries()` | The fixture's method counts the test's own thread or task. The module function counts every thread. | The same name has a different scope. | Keep. Both are documented. The fixture copies pytest-django's thread-local behaviour on purpose. | None. | — |
| 22 | `CallSite.filename` and `lineno`, the JSON keys `file` and `line` | The Python objects follow `traceback.FrameSummary`; the files and `app.Finding` use `file` and `line`. | Two spellings for one thing. | Keep. Each family is consistent within itself. | None. | — |
| 23 | Trend `--top` against compare `--rows` | Both set how many rows a table lists. | Two names for one idea. | Rename `--top` to `--rows` before the trend leaves experimental (no deprecation needed while experimental). `--rows` on compare is public, so it stays. | Trend users with `--top` (experimental). | — |
| 24 | MCP `compare` result, trend JSON, static-check JSON | In the MCP `compare` result, `new_findings` and `fixed_findings` are counts. In the trend JSON, `new_findings` and `gone_findings` are lists. Static-check findings have `rule` and `relationship`, not `kind` and `label`. | One key has different types and names across tools. | Keep all three experimental in 1.0, and align them before promoting any: count keys end in `_count`, and findings carry `kind` and `label`. | None in 1.0. | — |
| 25 | Action input `upload` | `true` uploads the SARIF file to code scanning. The JSON artifact is uploaded whenever `json-file` is set. | It reads as if it covered both files. | Keep it. The action is a SARIF action, and its description says what `upload` does. An `upload-sarif` alias would add an input for little gain. | None. | — |
| 26 | Action input `package`, and moving SQLAlchemy into the extra | Left empty, `package` installs the plugin from the action's own checkout, and the description says "pytest and SQLAlchemy come with it". | When a minor release moves SQLAlchemy into `[sqlalchemy]`, as #357 allows, the action's default install must install that extra or say why not. | Note it for the release that moves the dependency. | None now. | — |
| 27 | What stays experimental | `unused-eager-load`, `unused-columns`, the MySQL and MariaDB index check, the fix patch, the trend, the static check, the MCP server and `page_report`. | Each is either young (0.8.0), SQLAlchemy-only with no Django counterpart, or tested only in CI. | Keep all of them experimental in 1.0. Promote nothing else at rc1 apart from what #357 promotes: promoting is one-way, while staying experimental keeps every option open. | None. | — |
| 28 | Names that look like candidates and are not | `queryguard_max_queries` names both the ini option (the default budget) and the marker. `queryguard_max_events` counts statements and relationship loads. `NPlusOneDetected` also covers `column-load` and `write-loop`. `allow_nplusone` is a permanent alias. | — | Keep all of them. The shared ini and marker name is pytest-timeout's convention. "Events" is accurate. The exception name is the one users catch. | None. | — |
| 29 | Error messages as API | The compare marker `<!-- pytest-queryguard compare -->` is public on purpose. `tests/test_public_api.py` matches the deprecation warning's text "removed in pytest-queryguard 2.0". Nothing else is parsed. | The deprecation text is tested, but Stability promises only that the warning names the replacement and the release. | Keep. Matching the release number in the test is that promise. Other message texts stay outside the API, as Stability says. | None. | — |

## Django (#353) and the public names

The [Django design](https://github.com/MaciejZiel/pytest-queryguard/pull/353)
found the kinds, labels, file formats, markers, fixture and `__all__`
ORM-neutral. #357 rewords the texts that named SQLAlchemy and lets the
dependency move into an extra. This audit agrees and adds three items: the
`engine` parameter of `app.watch()` (item 4), the value name `session`
(item 7) and SQL-text labels (item 5). The SQLAlchemy-only options
(`queryguard_cartesian_load`, `queryguard_unused_eager_load`,
`queryguard_unused_columns`, `queryguard_show_patch`) keep their names. Under
Django they find nothing, or are not offered, which the Django docs already
say.

## Before rc1

1. Merge this change and #357.
2. In #357 or a follow-up: items 3 and 4.
3. Write down the decision on item 2 in the 1.0.0rc1 changelog entry.

Steps 1 and 2 shipped in 0.10.0. [From 0.10.0 to 1.0.0](design-1.0-release.md)
tracks what is left of this list, with the rest of the release.
