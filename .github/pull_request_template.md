## What and why

<!-- One or two sentences. Link the issue if there is one: "Fixes #123". -->

## Checklist

- [ ] Tests added or updated, and `uv run pytest -q` passes
- [ ] Code that scopes something to a request or unit: the cases in [CONTRIBUTING.md](https://github.com/MaciejZiel/pytest-queryguard/blob/main/CONTRIBUTING.md#when-code-scopes-something-to-a-request) are tested, or not applicable
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run mypy src` pass
- [ ] Documentation updated (`docs/`, and the README if the quick start changes); `uv run --group docs python scripts/build_docs.py --strict` passes
- [ ] `CHANGELOG.md` has an entry under `## [Unreleased]` for user-visible changes
- [ ] New third-party code or dependencies are recorded in `3rdparty_licenses.md` and none is copyleft (GPL, AGPL, LGPL, SSPL, MPL, EPL, CDDL)
