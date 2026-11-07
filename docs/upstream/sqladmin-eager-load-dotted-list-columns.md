# SQLAdmin: eager-load relationships on dotted list columns

Patch: `sqladmin-eager-load-dotted-list-columns.patch` (against `main` at
235a0a7). Branch name per CONTRIBUTING: `fix/eager-load-dotted-list-columns`.
Commit message: `fix: eager-load relationships on dotted list columns`.

---

**Title:** fix: eager-load relationships on dotted list columns

**Body:**

When `column_list` contains a dotted name such as `"user.profile.id"`, the
list page and the export only `selectinload` the first relationship
(`Address.user`). `User.profile` is not loaded, so reading it on the detached
row raises `DetachedInstanceError`, and `get_prop_value` falls back to
`_lazyload_prop`, which opens a new session and runs one query per row.

In the test suite, `AddressAdmin` has `column_list = ["id", "user_id", "user",
"user.profile.id"]`. `test_list_page_permission_actions` lists 10 addresses:

- before: `User.profile` is lazy-loaded 10 times, in 10 sessions; the test runs
  39 statements;
- after: the profiles are loaded with one `selectinload` query; 30 statements.

`test_export_csv_row_count` (export of 3 addresses) goes from 50 to 48
statements in the same way (3 lazy loads become one query).

The change builds the loader options once in `__init__`: for each list
column it follows the dotted name relationship by relationship and chains
`selectinload` (`selectinload(Address.user).selectinload(User.profile)`).
For plain relationship names this is the same `selectinload(relation)` as
before; names whose first part is not a relationship are skipped as before.
`list()` and `get_model_objects()` use these options instead of looping over
`_list_relations`.

The new test checks that listing and exporting addresses runs the same number
of statements for 1 and for 3 rows, and that the dotted value is still
exported.

I found this with [pytest-queryguard](https://github.com/MaciejZiel/pytest-queryguard),
a pytest plugin I am writing that reports lazy loads repeated per row, while
running it on SQLAdmin's test suite. The patch was checked against current
`main` on GitHub Actions (all tests pass, and `ruff check`, `ruff format
--check` and `mypy` pass): https://github.com/MaciejZiel/pytest-queryguard/actions/runs/42409673433.
