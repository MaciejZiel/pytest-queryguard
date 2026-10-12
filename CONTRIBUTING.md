# Contributing

Thanks for taking the time. Bug reports, documentation fixes and pull requests
are welcome. By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Reporting a bug

Use the [bug report form](https://github.com/MaciejZiel/pytest-queryguard/issues/new/choose).
The most useful report is a minimal reproduction: one test file with the
models, an in-memory SQLite engine, the test, and the output you got and the
output you expected. Include the versions of Python, pytest, SQLAlchemy and
pytest-queryguard. For vulnerabilities, follow [SECURITY.md](SECURITY.md)
instead of opening an issue.

## Development setup

The project uses [uv](https://docs.astral.sh/uv/). Python 3.10 or newer is needed.

```bash
git clone https://github.com/MaciejZiel/pytest-queryguard
cd pytest-queryguard
uv sync
```

## Running the checks

CI runs all of these; run them before you open a pull request.

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .      # uv run ruff format . fixes the formatting
uv run mypy src
uv run --group docs python scripts/build_docs.py --strict    # builds the site into site/
```

The suite measures itself with coverage in CI (`uv run coverage run -m pytest`
then `uv run coverage report`, at least 90 %). `coverage run` is used instead of
`--cov` so that the import-time lines of the plugin count.

### PostgreSQL tests

The missing-index tests need a PostgreSQL server and are skipped without one.
To run them, start a server and point `QUERYGUARD_PG_URL` at it:

```bash
docker run -d --rm --name qg-postgres -p 55432:5432 -e POSTGRES_PASSWORD=pg postgres:17
QUERYGUARD_PG_URL=postgresql+pg8000://postgres:pg@localhost:55432/postgres uv run pytest -q
docker stop qg-postgres
```

The index advisor's hypopg tests also need the `hypopg` extension in that
database; they are skipped when it is missing.

## Changes

- Branch from `main` and open a pull request against `main`. Keep each pull
  request to one topic, and each commit to one logical change with a message
  that says what changed and why.
- Add or update tests with the change. A bug fix starts with a test that fails.
- Update the documentation in `docs/` (and the README when the quick start
  changes). User-visible changes get a line under `## [Unreleased]` in
  [CHANGELOG.md](CHANGELOG.md).
- Do not bump the version or tag a release in a pull request.

## Dependencies and licenses

pytest-queryguard is MIT licensed. Any third-party code, library, GitHub
Action or copied text that you add, or whose version you change, must be
recorded in [3rdparty_licenses.md](3rdparty_licenses.md) in the same pull
request: one row with the source URL, the name, the SPDX license identifier
and a link to the license. `tests/test_licenses.py` checks the file. Copyleft
licenses (GPL, AGPL, LGPL, SSPL, MPL, EPL, CDDL) are not accepted for new
dependencies; that is why the PostgreSQL tests use pg8000 and asyncpg and not
psycopg. GitHub Actions are pinned by commit SHA.
