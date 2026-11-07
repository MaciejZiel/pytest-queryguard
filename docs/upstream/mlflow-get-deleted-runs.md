# MLflow: Select only run IDs in SqlAlchemyStore._get_deleted_runs

Patch: `mlflow-get-deleted-runs.patch` (against `master` at 529db93). Commit with `git commit -s`
(DCO sign-off is required; MLflow CI rejects unsigned commits). The PR is under
100 changed lines, so MLflow's auto-close policy does not require a linked
issue; the body must keep every heading of the PR template, as below.

---

**Title:** Select only run IDs in `SqlAlchemyStore._get_deleted_runs`

**Body:**

### Related Issues/PRs

None.

### What changes are proposed in this pull request?

`_get_deleted_runs` (used by `mlflow gc`) loads every deleted run as a full `SqlRun` object, all 14 columns, and then returns only `run_uuid`. This change adds `.with_entities(SqlRun.run_uuid)` to the same query, so it selects one column. The query keeps going through `_get_query`, so the workspace filter of `WorkspaceAwareSqlAlchemyStore` still applies (the test runs with workspaces disabled and enabled).

The number of queries does not change (one); the query reads 1 column per deleted run instead of 14, and builds no ORM objects.

I noticed this with [pytest-queryguard](https://github.com/MaciejZiel/pytest-queryguard), a pytest plugin I am writing, while running it on the SQLAlchemy store tests: it reported "SqlRun: 14 columns loaded, 12 never read" for this query.

### How is this PR tested?

- [x] Existing unit/integration tests
- [x] New unit/integration tests
- [ ] Manual tests

Added `test_get_deleted_runs_selects_only_run_ids`, which captures the statements of `_get_deleted_runs()` with a `before_cursor_execute` listener (as `test_hard_delete_run_does_not_load_child_rows` does) and checks that the `SELECT ... FROM runs` lists only `runs.run_uuid`. `tests/store/tracking/sqlalchemy_store/test_sqlalchemy_store_runs.py` passes on SQLite (419 passed, 8 skipped), and `ruff check` / `ruff format --check` pass on the changed files: https://github.com/MaciejZiel/pytest-queryguard/actions/runs/42409673433

### Does this PR require documentation update?

- [x] No.
- [ ] Yes. I've updated:
  - [ ] Examples
  - [ ] API references
  - [ ] Instructions

### Does this PR require updating the [MLflow Skills](https://github.com/mlflow/skills) repository?

- [x] No.
- [ ] Yes. Please link the corresponding PR or explain how you plan to update it.

### Release Notes

#### Is this a user-facing change?

- [x] No.
- [ ] Yes. Give a description of this change to be included in the release notes for MLflow users.

#### What component(s), interfaces, languages, and integrations does this PR affect?

Components

- [x] `area/tracking`: Tracking Service, tracking client APIs, autologging
- [ ] `area/models`: MLmodel format, model serialization/deserialization, flavors
- [ ] `area/model-registry`: Model Registry service, APIs, and the fluent client calls for Model Registry
- [ ] `area/scoring`: MLflow Model server, model deployment tools, Spark UDFs
- [ ] `area/evaluation`: MLflow model evaluation features, evaluation metrics, and evaluation workflows
- [ ] `area/gateway`: MLflow AI Gateway client APIs, server, and third-party integrations
- [ ] `area/prompts`: MLflow prompt engineering features, prompt templates, and prompt management
- [ ] `area/tracing`: MLflow Tracing features, tracing APIs, and LLM tracing functionality
- [ ] `area/projects`: MLproject format, project running backends
- [ ] `area/uiux`: Front-end, user experience, plotting, JavaScript, JavaScript dev server
- [ ] `area/build`: Build and test infrastructure for MLflow
- [ ] `area/docs`: MLflow documentation pages

<a name="release-note-category"></a>

#### How should the PR be classified in the release notes? Choose one:

- [x] `rn/none` - No description will be included. The PR will be mentioned only by the PR number in the "Small Bugfixes and Documentation Updates" section
- [ ] `rn/breaking-change` - The PR will be mentioned in the "Breaking Changes" section
- [ ] `rn/feature` - A new user-facing feature worth mentioning in the release notes
- [ ] `rn/bug-fix` - A user-facing bug fix worth mentioning in the release notes
- [ ] `rn/documentation` - A user-facing documentation change worth mentioning in the release notes

#### Is this PR a critical bugfix or security fix that should go into the next patch release?

<details>
<summary>What is a minor/patch release?</summary>

- Minor release: a release that increments the second part of the version number (e.g., 1.2.0 -> 1.3.0).
  Minor releases are expected to contain larger changes, such as new features and improvements. Non-critical bug fixes and doc updates can be included as well. By default, your PR should target the next minor release.
- Patch release: a release that increments the third part of the version number (e.g., 1.2.0 -> 1.2.1).
  Patch releases are typically only performed when there has been a major regression or bug in the latest release. For the sake of stability, your PR should not be included in a patch release unless it is a critical fix, or if the risk level of your PR is exceedingly low.

</details>

- [ ] This PR is critical and needs to be in the next patch release
- [x] This PR can wait for the next minor release
