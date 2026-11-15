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

### MySQL and MariaDB

`QUERYGUARD_TEST_DB_URL` runs the whole suite on a MySQL or MariaDB server:
every SQLite engine the tests create gets a fresh database there
(`tests/dbredirect.py`), and the tests about SQLite itself, marked
`sqlite_only`, keep SQLite. The URL names no database, and its user must be
allowed to create and drop databases. The live tests of the MySQL index
check take `QUERYGUARD_MYSQL_URL` or `QUERYGUARD_MARIADB_URL`
(`tests/test_mysql_index_check.py`).

```bash
docker run -d --rm --name qg-mysql -p 53306:3306 --tmpfs /var/lib/mysql \
  -e MYSQL_ROOT_PASSWORD=secret mysql:8.4 --max-connections=2000 \
  --mysql-native-password=ON --authentication-policy=mysql_native_password
QUERYGUARD_TEST_DB_URL=mysql+pymysql://root:secret@127.0.0.1:53306 uv run pytest -q -n 4
docker stop qg-mysql
```

A test that needs SQLite's own behavior (its SQL text, `PRAGMA`, the
`sqlite3` module, Python functions in SQL) is made dialect-aware, or marked
`@pytest.mark.sqlite_only("why")` when the behavior does not exist on MySQL.

## Changes

- Branch from `main` and open a pull request against `main`. Keep each pull
  request to one topic, and each commit to one logical change with a message
  that says what changed and why.
- Add or update tests with the change. A bug fix starts with a test that fails.
- Update the documentation in `docs/` (and the README when the quick start
  changes). User-visible changes get a line under `## [Unreleased]` in
  [CHANGELOG.md](CHANGELOG.md).
- Do not bump the version or tag a release in a pull request.

CI on a pull request runs a subset of the matrix in `.github/workflows/ci.yml`:
lint, docs, the oldest and the newest Python with the oldest and the latest
pytest and SQLAlchemy, Windows, one PostgreSQL, MySQL, Django on SQLite and on
PostgreSQL, xdist, free-threaded Python, the plugins, the overhead gate and
the action: 15 jobs instead of 43. The `all-green` job fails when any of
them failed or was cancelled. A push to `main` and the release gate run all
of it. To run all of it on a pull request (a change to the CI, to a
database or operating system specific path, or anything risky), add the
`full-ci` label: `.github/workflows/full-ci.yml` runs the whole matrix at
once, and every later push runs it while the label stays.

### When code scopes something to a request

Most bugs found from outside so far had one cause: a scope (a request, a
`track()` unit, a recording) tied to a call frame, while the work it
covers starts before or ends after that frame. A change that sets, resets or
reads such a scope (a `ContextVar`, a token, a signal handler, a WSGI or
ASGI middleware, the Django backend's request patches) needs a test for
each of these that applies, with a statement run in that phase and a check
that it lands in its own request and nowhere else (no other request, no
enclosing `track()`):

- the body produced after the handler returned: a sync and an async
  generator, a `StreamingHttpResponse`/`StreamingResponse`, read by a test
  client after its call returned (#386);
- the response iterable's `__iter__` and `close()`, close callbacks
  (`call_on_close`), and a body closed unread or half read (#203);
- a file the server sends: `wsgi.file_wrapper`, `FileResponse`,
  `http.response.pathsend` (#211);
- background tasks, sync (thread pool) and async, after the response;
- an exception half way through the body: the request still ends, once,
  and the next one starts clean;
- a `HEAD` request;
- the order of middleware: ours outside and inside another
  (`BaseHTTPMiddleware`), and an application mounted under a prefix
  (`root_path`, `SCRIPT_NAME`) (#210);
- threads and tasks: a test client running the app in its own thread,
  concurrent requests on one event loop, bodies of several requests read in
  turn, `asyncio.gather` running receivers in tasks of their own (#386);
- a scope that spans a generator: the consumer's code between items is not
  inside it, two generators read in turn each end their own, and a scope
  entered inside the generator across a `yield` stays its current one (the
  `track()` decorator);
- work that finishes after recording ended: a statement started in the
  scope and completed in another thread (#202).

`tests/inapp/test_request_lifecycle.py` is the matrix for the in-app
middleware and `track()`; add a row there (or to the Django tests) rather
than a test of one framework. Tests that start a Python subprocess with
`-I` pass `-X utf8` and read its output as UTF-8 (#287).

## Releasing

Only a maintainer releases, and a release never starts with a tag: it starts
with the release gate, the workflow `.github/workflows/release-gate.yml`. It
runs the whole CI matrix and adds the oldest SQLAlchemy and pytest on Windows
and macOS, the accuracy corpus against the minimums recorded in
`scripts/accuracy_thresholds.json` and the static check against those in
`scripts/lint_accuracy_thresholds.json`, the code examples in the
documentation, a clean-environment install of the wheel and the sdist, an
upgrade from the previous PyPI release, the overhead budget against
`scripts/bench_baseline.json`, the contents of the wheel and the sdist, and
the agreement of the version, the changelog entry, its date and its links. It
runs on a pull request that changes the version and by hand from the Actions
tab. See [docs/releasing.md](docs/releasing.md) for the list and for running
the checks locally (`uv build --out-dir dist` and `uv run python
scripts/release_gate.py all dist`).

If a check fails because the code got worse, fix the code. Change a threshold
only in a pull request that explains why.

## Dependencies and licenses

pytest-queryguard is MIT licensed. Any third-party code, library, GitHub
Action or copied text that you add, or whose version you change, must be
recorded in [3rdparty_licenses.md](3rdparty_licenses.md) in the same pull
request: one row with the source URL, the name, the SPDX license identifier
and a link to the license. `tests/test_licenses.py` checks the file. Copyleft
licenses (GPL, AGPL, LGPL, SSPL, MPL, EPL, CDDL) are not accepted for new
dependencies; that is why the PostgreSQL tests use pg8000 and asyncpg and not
psycopg. A copyleft package may be installed in a CI job as a test-only tool
that is not in `uv.lock` and not shipped (psycopg 3 in the `postgres` job);
record it in `3rdparty_licenses.md` all the same. GitHub Actions are pinned by commit SHA.
