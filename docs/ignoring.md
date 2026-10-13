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

The patterns match every kind of finding, `missing-index` included, so
`@pytest.mark.allow_nplusone("public.audit_log", kind="missing-index")` leaves
out one table's finding. The bare marker leaves out N+1 findings only;
`allow_missing_index` is the shortcut for skipping the index check for the test
or for some tables.

A table name is matched the same way in all four places that take one
(`allow_nplusone(..., kind="missing-index")`, `queryguard_ignore`,
`allow_missing_index` and `queryguard_index_ignore_tables`), so one spelling
works everywhere. A pattern is a table name or `schema.table`, and each part is
an identifier:

- unquoted (`audit_log`, `UserAccount`, `audit_*`): compared as written
  (case-sensitive, `UserAccount` does not match `useraccount`), with `fnmatch`
  wildcards;
- double-quoted (`"UserAccount"`, `public."UserAccount"`, `"my.table"`):
  compared exactly, with no wildcards, and may hold dots (`""` is a quote).

A bare name matches the table in any schema; `public.audit_log` matches the
table `audit_log` in the schema `public` also when the report prints it as
`audit_log` because that schema is on the search path. The label as printed
(`"UserAccount"`) matches too, and so does any `fnmatch` pattern on `table` or
`schema.table` (`audit*` also matches every table in the schema `audit`). Schemas are
known on PostgreSQL only; on SQLite and MySQL only bare names match.

In `queryguard_ignore` and `allow_nplusone`, this makes a bare name match the
table in every schema: `missing-index audit_log` also leaves out the finding
labelled `audit.audit_log`.

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
`missing-index` (the table, with its schema as in the report; see below for
the spellings a table name accepts). `file` is
relative to pytest's rootdir, with forward slashes. Findings that no longer
exist are easy to forget, so a pattern that matched nothing in the run is
listed in the terminal summary under "stale ignores" (not shown with
pytest-xdist, where the workers do the matching). There is no
`with` block form: the collector is process-wide, so a block could not
tell its own statements from those of other threads.

