# Ignoring findings

Three mechanisms leave findings out, from the narrowest to the widest: the
`allow_nplusone` marker (one test), `queryguard_ignore` (the whole suite) and
a [baseline](baseline.md) (everything that exists today). Budgets are never
affected by them.

## The marker and `queryguard_ignore`

```python
@pytest.mark.allow_nplusone
def test_legacy_report(session): ...
```

The test is not checked for N+1. A `max_queries` marker on the same test is
still enforced.

To leave out one finding instead of the whole test, give the marker patterns
(`fnmatch`, case-sensitive) for the finding's label, and optionally `kind`,
`file` and `function`. A label written out exactly as the report prints it
matches too, even when it holds `[` or `]`, which `fnmatch` would read as a
character class:

```python
@pytest.mark.allow_nplusone("Order.customer", function="list_orders")
def test_legacy_report(session): ...
```

The same patterns work for the whole suite in `queryguard_ignore`, one per
line as `KIND LABEL [file=GLOB] [function=GLOB]`:

```toml
[tool.pytest.ini_options]
queryguard_ignore = [
    "relationship-load Order.customer",
    "repeated-statement SELECT * FROM settings*",
    "missing-index public.audit_log",
]
```

Every finding in the report has a line such as
`kind: relationship-load  label: Order.customer`; write the kind, a space and
the label as the pattern. The kinds are `relationship-load` (the label is the relationship, as in
`Order.customer`), `repeated-statement` (the normalized statement),
`column-load` (the class and its deferred columns, as in `Book.summary`, or
the class and the cause for expired attributes, as in `Book (expired)`),
`write-loop` (the normalized `INSERT`, `UPDATE` or `DELETE`),
`unused-eager-load` (the relationship, as in `Order.customer`) and
`missing-index` (the table, with its schema as in the report). `file` is
relative to pytest's rootdir, with forward slashes. Findings that no longer
exist are easy to forget, so a pattern that matched nothing in the run is
listed in the terminal summary under "stale ignores" (not shown with
pytest-xdist, where the workers do the matching). There is no
`with` block form: the collector is process-wide, so a block could not
tell its own statements from those of other threads.

