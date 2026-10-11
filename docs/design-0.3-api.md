# pytest-queryguard 0.3: reading the measured queries in a test

| | |
|---|---|
| Status | Accepted |
| Author | Maciej Zieliński |
| Date | 2026-10-11 |
| Target release | 0.3.0 |
| Extends | [design.md](design.md), section 4 |

## 1. Problem

Tests sometimes need more than a pass or fail from the plugin. They may
want to assert on the SQL a block sent, print it while debugging, or run
the N+1 rules on one block instead of the whole test. In 0.2 the only way
to do that is the object that `with max_queries(n) as c` yields. It is
undocumented and internal: `QueryCollector`, with lists of event classes
that also carry the index check's working state.

## 2. Goals

1. A small, documented, typed way to record the statements a block of code
   sends and read them back: SQL text, call site and relationship.
2. A count, and a helper that runs the N+1 rules on the recorded block.
3. It works in pytest and outside it, like `max_queries()`.
4. Nothing in 0.2 changes behaviour. Internal classes stay private, so
   they can change without breaking users.

Non-goals: parameter values, timings, row counts, filtering helpers, and
access to the index check's findings. They can be added later without
breaking this API.

## 3. API

```python
from pytest_queryguard import record_queries

with record_queries() as log:
    list_orders(session)

assert log.count == 2
for query in log.queries:
    print(query.sql, query.callsite, query.relationship)
log.assert_no_nplusone()
```

Exported from `pytest_queryguard`:

| Name | Kind | Meaning |
|---|---|---|
| `record_queries()` | context manager | Records every SQL statement SQLAlchemy sends inside the block, from any thread, and yields a `QueryLog`. |
| `QueryLog` | class | `queries` is a tuple of `Query`, in the order they ran. `count` is their number. Both are live inside the block and fixed after it. The log is also sized, iterable and indexable (`len()`, `for`, `log[i]`, `log[a:b]`) but not a `collections.abc.Sequence` (`count` is a number, not `count(value)`). It is not constructible by users. `assert_no_nplusone(relationship_threshold=2, repeat_threshold=5)` raises `NPlusOneDetected` with the plugin's own failure text. |
| `Query` | frozen dataclass | `sql` is the statement text as sent to the driver. `callsite` is a `CallSite`, or `None` when no frame outside libraries was found. `relationship` is `"Order.customer"` when the statement loaded that relationship, lazily or as the extra query of `selectinload`, otherwise `None`. |
| `CallSite` | frozen dataclass | `filename` (absolute), `lineno`, `function`: the first frame outside SQLAlchemy, pytest, the standard library, `site-packages` and the plugin. |
| `QueryGuardInternalWarning` | warning | A subclass of `UserWarning`. Issued when a block ends after an internal error, because its log may be incomplete. |
| `NPlusOneDetected` | exception | A subclass of `AssertionError`, like `QueryBudgetExceeded`. |

## 4. Decisions

- **A context manager, not a fixture.** A context manager covers a whole
  test (wrap the body) and a part of one. It also works outside pytest,
  in a script or a benchmark, as `max_queries()` does. A `queryguard`
  fixture would only add access to the plugin's own measurement of the
  test body. Its value is created during fixture setup, before that
  measurement starts, so it would need a second, lazily bound object. That
  can come later if users ask for it.
- **`max_queries()` is left alone.** It still yields the internal
  collector, as in 0.2, so code that read `c.statements` keeps working.
  The README documents `record_queries()` for reading queries, and the two
  combine: `with max_queries(3), record_queries() as log:`.
- **Public value types are separate from the internal events.** `Query` is
  built from the internal `StatementEvent` when `queries` is read. Internal
  events can then gain fields without changing the API. `CallSite` was
  already a small frozen value with the three fields users need, so it is
  exported as it is.
- **N+1 thresholds are keyword arguments with the ini defaults (2 and 5).**
  The helper also works outside pytest, where there is no configuration.
  Taking the values from the running session's ini options would make the
  same call behave differently in different projects. Invalid thresholds
  raise `ValueError`, as an invalid `max_queries` limit does.
- **The helper ignores `queryguard_mode` and `allow_nplusone`.** Calling
  it is an explicit request, like a budget.
- **An internal error is never silent.** The listeners switch themselves
  off after an internal error, which would leave an empty log. When the
  block ends and an error is recorded, whether during the block or before it
  (it had already stopped the recording), `record_queries()` issues
  `QueryGuardInternalWarning`, and `assert_no_nplusone()` raises
  `RuntimeError` instead of passing on a possibly empty log. The state is
  fixed when the block ends. This does not depend on pytest or on the mode.
- **Paths in messages** are relative to pytest's rootdir inside pytest and to
  the working directory outside it.
- **Recording does not change the plugin's own checks.** Measurements
  stack. Every active measurement gets every statement, so the plugin's N+1
  detection, budgets and index check see the same queries with or without
  a `record_queries()` block inside the test.

## 5. Compatibility

Additive only. No existing name, option, marker or message changes. The
new names are listed in `pytest_queryguard.__all__`. `py.typed` already
ships, and the new code passes `mypy --strict`. A test also type-checks a
user snippet against the public API.
