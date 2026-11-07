# Upstream fixes

Fixes for true findings of the real-world run (docs/accuracy.md, "Real
projects"), prepared as patches against the projects' default branches, with
a ready PR title and body each. Nothing here has been sent upstream.

Checked on GitHub Actions by `.github/workflows/upstream-fixes.yml` (runs on
pushes to `upstream/fixes` only): each patch is applied to the project's
default branch, its test files run before and after, without and with
pytest-queryguard, and lint runs on the result. Run with all four jobs green:
https://github.com/MaciejZiel/pytest-queryguard/actions/runs/42409673433
(MLflow `master` at 529db93, SQLAdmin `main` at 235a0a7).

| patch | PR text | result |
|---|---|---|
| `mlflow-get-deleted-runs.patch` | `mlflow-get-deleted-runs.md` | 1 query either way; 1 column instead of 14; finding 2 → 0 |
| `mlflow-log-inputs-dataset-columns.patch` | `mlflow-log-inputs-dataset-columns.md` | 1 query either way; 3 of 8 columns, no schema/profile; findings 12 → 0 |
| `sqladmin-eager-load-dotted-list-columns.patch` | `sqladmin-eager-load-dotted-list-columns.md` | list of 10 rows: 10 lazy loads in 10 sessions → 1 query (39 → 30 statements); findings 4 → 0 |
| `sqladmin-export-load-exported-relationships.patch` (on top of the previous one) | `sqladmin-export-load-exported-relationships.md` | export tests 6 → 2, 9 → 5, 48 → 44 statements; findings 46 → 0 |

Dropped:

- MLflow `search_traces` lazy-loading `tags` and `request_metadata` per trace:
  fixed upstream by mlflow/mlflow#22431 (merged 2026-04-08); `master` now loads
  them in batches (`_build_trace_infos_from_rows`).
- SQLAdmin "delete selected" (one session and one lazy load per row): the
  per-object `delete_model`, `on_model_delete` and `after_model_delete` hooks
  are the public API, and the lazy load of `complaints` is the ORM's delete
  cascade. Batching it is a design change, not a small fix.

Contribution rules:

- MLflow: DCO sign-off on every commit (`git commit -s`, real name); the PR
  body must keep the template headings or a bot closes it; a PR over 100
  changed lines must link an issue, and one that closes an issue needs the
  `ready` label on it (both patches are under 100 lines, so no issue needed);
  one concern per PR; no changelog entry (generated at release). Their
  AGENTS.md asks for a `Co-Authored-By: Claude` trailer on commits written
  with Claude Code: the owner decides.
- SQLAdmin (now `smithyhq/sqladmin`): Conventional Commits (`fix: ...`), a
  `fix/...` branch, tests required, coverage must stay at 95 % or more, one
  concern per PR, `make lint` (ruff, mypy) passing; no DCO, no issue first, no
  changelog entry (written by the maintainers at release).
