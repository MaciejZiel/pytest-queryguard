# Running queryguard in CI and in GitHub code scanning

pytest-queryguard already fails the test run on a new N+1 query, so any CI
that runs pytest enforces it. To also see the findings in the GitHub UI, as
code scanning alerts with a file, a line and a fix hint, use the action in
this repository or upload the [SARIF file](sarif.md) yourself.

## The action

```yaml
name: Tests
on: [push, pull_request]

permissions:
  contents: read

jobs:
  tests:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      security-events: write   # needed to upload to code scanning
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[test]"        # your project and its test dependencies
      - uses: MaciejZiel/pytest-queryguard/action@COMMIT_SHA   # pin a release tag or a commit SHA
        with:
          pytest-args: -x tests/
          baseline: queryguard-baseline.json
```

The action installs `pytest-queryguard` into the active Python, runs
`pytest --queryguard-sarif=queryguard.sarif`, uploads the file with
[`github/codeql-action/upload-sarif`](https://github.com/github/codeql-action)
(pinned by commit SHA inside the action) and then fails the step if pytest
failed. Install your own project first: the action does not know how.

| Input | Default | Meaning |
|---|---|---|
| `pytest-args` | empty | Arguments for pytest, as a list of shell words: quotes group words (`-k "a or b"`). The value is split by Python's `shlex` and is not evaluated by a shell, so `$(...)`, backticks and `$VAR` are passed to pytest as literal text. |
| `python-version` | empty | Set up this Python. Empty: use the `python` on PATH. |
| `package` | `pytest-queryguard` | What to `pip install`: a requirement like `pytest-queryguard>=0.4`, or a path. |
| `baseline` | empty | Baseline file, see [baseline.md](baseline.md). Empty: `queryguard_baseline` from your config. |
| `fail` | `true` | `true`: fail the step when pytest fails. `false`: never fail, only report. |
| `working-directory` | `.` | Where to run pytest. Locations in the SARIF file are still relative to the repository root (see [SARIF output](sarif.md#paths-and-the-repository-root)). |
| `sarif-file` | `queryguard.sarif` | Output file, relative to `working-directory`. |
| `upload` | `true` | Upload to code scanning. `false`: only write the file. |
| `category` | `pytest-queryguard` | Code scanning category; use one per job when several jobs upload. |

Outputs: `exit-code` (pytest's) and `sarif-file`.

A typical rollout: run once with `fail: "false"` to see the alerts, create a
[baseline](baseline.md) for what you cannot fix yet, then switch to
`fail: "true"` (the default).

## Permissions and forks

- Uploading needs `security-events: write` on the job (or the workflow).
  Without it, the upload step fails with "Resource not accessible by
  integration".
- **Pull requests from forks get a read-only token**, and GitHub does not
  allow them to upload. The action skips the upload for them and still runs
  pytest, so the test result is unaffected; the findings are in the job log.
  The same holds for runs started by Dependabot (`github.actor` is
  `dependabot[bot]`), which also have a read-only token: the upload is skipped
  for them too.
- The upload step has `continue-on-error: true`: an upload that GitHub refuses
  (for example a read-only token in a case the action does not recognize, such
  as `pull_request_target`, or code scanning turned off) shows as a failed step
  and a warning, but does not fail a job whose tests passed. The flip side is
  that a missing `security-events: write` also does not fail the job; look at
  the step in the log if alerts do not appear. Set `upload: "false"` for
  events where you do not want an upload at all.
- Code scanning is free for public repositories. Private repositories need
  GitHub Code Security.

## Without the action

```yaml
      - run: pytest --queryguard-sarif=queryguard.sarif
        continue-on-error: true
      - uses: github/codeql-action/upload-sarif@v4
        if: always()
        with:
          sarif_file: queryguard.sarif
          category: pytest-queryguard
```

## How this repository tests the action

The `action` job in `.github/workflows/ci.yml` runs the local action
(`uses: ./action`) on `examples/action-sample`, a project with one N+1 query:
once with `fail: "false"` (the SARIF file is checked, including that its URI
starts with `examples/action-sample/`, the working directory), once expecting
the step to fail, and once with the finding in a baseline. None of them
uploads: a sample upload with no results would make every pull request report
"1 configuration not found" in its checks.
