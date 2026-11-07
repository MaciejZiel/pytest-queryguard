# SQLAdmin: load the relationships of the export columns when exporting

Patch: `sqladmin-export-load-exported-relationships.patch`. It applies on top
of `sqladmin-eager-load-dotted-list-columns.patch` (it reuses
`_build_relation_loads`), so open it after that one is merged, or as a
follow-up that says it depends on it. Branch name:
`fix/export-load-exported-relationships`. Commit message:
`fix: load the relationships of the export columns when exporting`.

---

**Title:** fix: load the relationships of the export columns when exporting

**Body:**

`get_model_objects()`, used by the export endpoint, adds `selectinload` for
every relationship of the *list* page, but the export writes the
*export* columns (`column_export_list`). Two things follow:

- relationships that are listed but not exported are still loaded: one extra
  query per relationship, and all related rows of the exported objects;
- a relationship that is exported but not listed is not loaded, so each row
  reads it through `_lazyload_prop`, with a new session and a query per row.

In the test suite, `UserAdmin` lists four relationships and exports
`[User.name, User.status]`. `test_export_csv` and `test_export_json` go from
6 to 2 statements, `test_export_csv_row_count` from 48 to 44, and so on for
the other export tests (sync and async).

The change builds the loader options for the export columns once in
`__init__` and uses them in `get_model_objects()`. With
`use_pretty_export = True` the export also runs the list formatters, which
may read relationships of the list page, so in that mode the list page's
relationships are still loaded as well.

The new test exports users with `column_list = [User.id, User.addresses]` and
`column_export_list = [User.name, User.profile]`: it checks the output, that
no query reads `addresses`, and that the number of statements is the same for
1 and for 3 users.

I found this with [pytest-queryguard](https://github.com/MaciejZiel/pytest-queryguard),
a pytest plugin I am writing that also reports eager loads that are never
read, while running it on SQLAdmin's test suite. The patch was checked against
current `main` on GitHub Actions (all tests pass, and `ruff check`, `ruff
format --check` and `mypy` pass): https://github.com/MaciejZiel/pytest-queryguard/actions/runs/42409673433.

One thing to decide: `get_model_objects()` is only called by the export
endpoint, but it is a public method. If someone calls it for something else,
it now loads the relationships of the export columns, not of the list
columns.
