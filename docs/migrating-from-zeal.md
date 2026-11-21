# Migrating from django-zeal

[django-zeal](https://github.com/taobojlen/django-zeal) is the established
N+1 detector for Django: it patches the ORM's relation descriptors and raises
`NPlusOneError` (or warns) when one relation is lazy-loaded twice from one
line. pytest-queryguard checks Django code in tests too ([Django](django.md)),
so a project that uses zeal only to fail tests can replace it. This page maps
each zeal setting, function and integration to its pytest-queryguard
equivalent, says plainly where there is none, and shows a migration that runs
both tools side by side first.

It is written by the author of pytest-queryguard. Everything said about zeal
is of django-zeal 2.3.0 (2026-10-14), the current release, **read on
2026-11-21** in its [README](https://github.com/taobojlen/django-zeal/blob/v2.3.0/README.md)
(the same on the `main` branch that day), its
[changelog](https://github.com/taobojlen/django-zeal/blob/v2.3.0/CHANGELOG.md)
and its installed source (`zeal/listeners.py`, `zeal/patch.py`,
`zeal/middleware.py`, `zeal/signals.py`). The runs on this page were made the
same day with Python 3.14.4, Django 6.1.3, pytest 9.2.0, pytest-django
4.14.0, django-zeal 2.3.0 and pytest-queryguard 1.1.0, on SQLite. zeal has no
documentation site: the README is its documentation.

## Before you move

zeal is not only a test tool. Its README sets it up in four places, and
pytest-queryguard covers one of them:

| Where zeal runs | pytest-queryguard |
|---|---|
| Tests, through an autouse fixture with `zeal_context()` or a unittest mixin | Yes: every pytest-django test, with nothing to set up, or `manage.py test` with `QueryGuardRunner` ([Without pytest](django.md#without-pytest-managepy-test)) |
| Web requests of the running app (`zeal.middleware.zeal_middleware`, with `DEBUG`) | No. The [in-application mode](in-app.md) is for SQLAlchemy only, and Django is [out of its scope](django.md#out-of-scope-for-django). Requests of the test client are checked |
| Celery workers (`setup()` and `teardown()` on `task_prerun` and `task_postrun`) | No. A task the test runs eagerly, in the test's own thread, is measured as part of the test (not tested with Celery here) |
| Any code, with `setup()`, `teardown()` or `zeal_context()` | No: outside tests, nothing is measured |

If you rely on zeal in the development server or in workers, keep it for
that: pytest-queryguard does not replace it there. You can still drop zeal
from the test run (see [step 5](#step-by-step)).

## The main differences in behaviour

- **What is measured.** zeal measures whatever runs inside `zeal_context()`.
  With the README's autouse fixture, that is the test body and the fixtures
  set up after it, so data created in a fixture can fail a test
  ([django-zeal#19](https://github.com/taobojlen/django-zeal/issues/19)).
  pytest-queryguard measures the test function body only, and by default
  leaves out the N+1s of test code (a loop in the test, a fixture, a factory,
  a helper under `tests/`) and counts them at the end of the run
  ([Test code](how-detection-works.md#test-code)). After the move, findings
  that zeal raised in test code disappear; `queryguard_test_code = "warn"`
  shows them.
- **When it fails.** zeal raises `NPlusOneError` at the second lazy load, in
  the middle of the test. pytest-queryguard reports when the test ends, with
  every load counted. `queryguard_strict = true` raises
  `pytest_queryguard.NPlusOneDetected` at the access instead
  ([Strict mode](when-a-test-fails.md#strict-mode-fail-at-the-access)).
- **What counts as an N+1.** zeal counts loads of one model and field from
  one line of your code, and ignores objects loaded on their own (with
  `.get()`, `.first()` or a one-row slice). Its threshold, 2 by default, is
  the same for relations, deferred fields and `.get()`. pytest-queryguard
  counts a relation lazy-loaded for 2 different parents of one query line
  (`relationship-load`), the same `SELECT` run 5 times (`repeated-statement`,
  which covers zeal's `.get()` in a loop and more), and a deferred field read
  for 5 objects (`column-load`). So a `.get()` loop of 2 to 4 rows, which zeal
  reports, passes here unless you set `queryguard_small_repeat_threshold = 2`
  (checked with 3 rows: zeal reported `library.Book.get()`, pytest-queryguard
  passed, and with the option it reported `the same statement ran 3 times
  with different parameters`).
- **Labels.** zeal names `app_label.Model.field` (`library.Book.author`) and
  `library.Book.get()`. pytest-queryguard labels a relation `Book.author`,
  without the app label, and a repeated statement by its SQL, normalized.

## Find and replace

What to change, line by line. The tables below explain each row.

| Find | Replace with |
|---|---|
| `"zeal"` in `INSTALLED_APPS` | Delete it; nothing goes into `INSTALLED_APPS` |
| `"zeal.middleware.zeal_middleware"` in `MIDDLEWARE` | Delete it if it was there for the tests: test-client requests are measured anyway |
| The autouse fixture with `with zeal_context(): yield` | Delete it: every test body is measured |
| A `ZealTestMixin` (`setUp()` calls `zeal.setup()`) | `TEST_RUNNER = "pytest_queryguard.django.QueryGuardRunner"`, or the `QueryGuardTestCase` mixin |
| `ZEAL_RAISE = True` (the default) | Nothing: `queryguard_mode = "fail"` is the default |
| `ZEAL_RAISE = False` | `queryguard_mode = "warn"` |
| `ZEAL_NPLUSONE_THRESHOLD = 3` | `queryguard_relationship_threshold = 3` (and the other thresholds, see below) |
| `ZEAL_SHOW_ALL_CALLERS = True` | `queryguard_stack_depth = 10` (3 callers by default) |
| `ZEAL_ALLOWLIST = [{"model": "library.Book", "field": "author"}]` | `queryguard_ignore = ["relationship-load Book.author"]` |
| `{"model": "library.Book"}` (every field of a model) | `"relationship-load Book.*"` |
| `{"model": "library.Book", "field": "get()"}` | `'repeated-statement SELECT * FROM "library_book" WHERE *'`, or `"repeated-statement * function=titles"` for one function |
| `ZEAL_CHECK_UNUSED_IGNORES = True` | Nothing: unused patterns, markers and `ignore()` blocks are always listed as "stale ignores" (they do not fail the run) |
| `with zeal_ignore():` in a test | `@pytest.mark.queryguard_ignore` on the test |
| `with zeal_ignore([{"model": "library.Book", "field": "author"}]):` in a test | `@pytest.mark.queryguard_ignore("Book.author")` |
| `with zeal_ignore():` in application code | `with pytest_queryguard.ignore("*"):`, or a pattern with `function=` (see [below](#zeal_ignore)) |
| `pytest.raises(zeal.NPlusOneError)` | `record_queries()`, then `pytest.raises(pytest_queryguard.NPlusOneDetected)` around `log.assert_no_nplusone()`, with `@pytest.mark.queryguard_ignore` on the test so that the plugin does not fail it for the same N+1 |
| `@receiver(nplusone_detected)` | No equivalent; see [Signals and logging](#signals-and-logging) |

## Settings

zeal reads five settings from the Django settings in 2.3.0
(`zeal/listeners.py`). There is no `ZEAL_ENABLED`: zeal is on where
`zeal_context()`, `setup()` or the middleware turned it on, and its patches
are installed when `"zeal"` is in `INSTALLED_APPS`.

| django-zeal 2.3.0 | Default | pytest-queryguard |
|---|---|---|
| `ZEAL_RAISE` | `True` | `queryguard_mode`: `fail` (the default) raises nothing in the code but fails the test when it ends; `warn` issues a `QueryGuardWarning`, as `ZEAL_RAISE = False` issues a `UserWarning`. `off` turns it off. `queryguard_fail_on` lets some kinds fail and others warn, which zeal cannot do (it has one kind) |
| `ZEAL_NPLUSONE_THRESHOLD` | `2` | Three options: `queryguard_relationship_threshold` (`2`, relations), `queryguard_repeat_threshold` (`5`, repeated `SELECT`s, which includes `.get()`) and `queryguard_column_load_threshold` (`5`, deferred fields). `queryguard_small_repeat_threshold = 2` reports 2 to 4 runs from one line with different parameters |
| `ZEAL_ALLOWLIST` | `[]` | `queryguard_ignore`, one pattern per entry; see [Allow-list entries](#allow-list-entries) |
| `ZEAL_SHOW_ALL_CALLERS` | `False` | `queryguard_stack_depth`: every report already shows the line, up to 3 callers (`called from`) and, for a relation, the line that loaded the parents. Raise it for a deeper stack. zeal prints the stack of each of the calls; the report here shows one stack, of the first |
| `ZEAL_CHECK_UNUSED_IGNORES` (new in 2.3.0) | `False` | Always on, in another form: see [Unused ignores](#unused-ignores) |

Under `manage.py test` the options go into one dict in the settings, with the
names of the ini options without the `queryguard_` prefix
([Without pytest](django.md#without-pytest-managepy-test)):

```python
TEST_RUNNER = "pytest_queryguard.django.QueryGuardRunner"
QUERYGUARD = {
    "mode": "fail",
    "relationship_threshold": 2,
    "ignore": ["relationship-load Book.author"],
}
```

### Allow-list entries

A `ZEAL_ALLOWLIST` entry is a model (`app_label.Model`) and an optional field,
both `fnmatch` patterns. A `queryguard_ignore` pattern is a kind and a label,
also `fnmatch`, with optional `file=`, `function=` and `stack=`
([Ignoring findings](ignoring.md)):

| `ZEAL_ALLOWLIST` entry | `queryguard_ignore` pattern |
|---|---|
| `{"model": "library.Book", "field": "author"}` | `relationship-load Book.author` |
| `{"model": "library.Book"}` | `relationship-load Book.*`; add `column-load Book*` for deferred fields |
| `{"model": "library.Book", "field": "summary"}` (a field left out by `.only()`) | `column-load Book.summary` |
| `{"model": "library.Book", "field": "get()"}` | `repeated-statement SELECT * FROM "library_book" WHERE *` (the label is the SQL; quote the pattern in TOML with `'...'`) |
| `{"model": "library.*"}` (a whole app) | No label pattern: labels have no app label. `file=library/*` leaves out the findings whose line is in the app's code, which is not the same thing |

What differs:

- zeal checks the model and field names of `ZEAL_ALLOWLIST` against the
  installed models and raises `ZealConfigError` for a name that does not
  exist (unless it holds a wildcard). pytest-queryguard rejects an unknown
  kind as a usage error, but a label that matches nothing is only listed as a
  stale ignore at the end of a full run.
- A zeal entry applies wherever the N+1 happens. A pattern can also be
  narrowed to a file, a function or a caller (`stack=`).

## Functions and integrations

| django-zeal 2.3.0 | pytest-queryguard |
|---|---|
| `zeal_context()` (context manager and decorator) | In a test: nothing, the body is measured; to check one block, `with record_queries() as log:` and then `log.assert_no_nplusone()`. Outside tests: no equivalent |
| `setup()` and `teardown()` | No equivalent: the plugin starts and stops measuring around each test |
| `zeal_ignore()` | `pytest_queryguard.ignore("*")`, a block or decorator ([the `ignore()` block](ignoring.md#the-ignore-block)) |
| `zeal_ignore([{"model": ..., "field": ...}])` | `ignore()` takes finding kinds, not labels: `ignore("relationship-load")` leaves out every relation N+1 in the block. For one label, use the marker with a pattern on the test, or `queryguard_ignore` with `function=` |
| `zeal.middleware.zeal_middleware` (sync and async) | No middleware. Requests made with the test client, `AsyncClient` or the live server are measured as part of the test, each request counted on its own |
| `zeal.signals.nplusone_detected` | No equivalent |
| `ZealError`, `NPlusOneError` | `pytest_queryguard.NPlusOneDetected`, raised by `log.assert_no_nplusone()` and in strict mode. A normal failure is the test's own failure, not an exception in your code |
| `UnusedIgnoreError` | No exception: the "stale ignores" section of the terminal summary |
| `ZealConfigError` (an allow-list name that does not exist) | No check of names; an unknown kind is a usage error |
| A `ZealTestMixin` for unittest test cases | `QueryGuardRunner` as `TEST_RUNNER`, or `QueryGuardTestCase` mixed into the test cases ([Without pytest](django.md#without-pytest-managepy-test)) |

### zeal_ignore

In a test, a `zeal_ignore()` block becomes a marker on the test. The marker
takes the label patterns that `zeal_ignore()` took as model and field:

```python
import pytest


@pytest.mark.queryguard_ignore("Book.author")
def test_book_lines(books):
    assert len(book_lines()) == 5
```

In application code, `pytest_queryguard.ignore()` does what `zeal_ignore()`
did, by finding kind:

```python
from pytest_queryguard import ignore

from library.models import Book


def nightly_lines():
    with ignore("relationship-load", reason="nightly export, accepted"):
        return [f"{book.title} by {book.author.name}" for book in Book.objects.all()]
```

Outside a measured test the block only sets a context variable, but the
module has to be importable wherever that code runs, and pytest-queryguard
depends on pytest and SQLAlchemy. zeal had the same need (`from zeal import
zeal_ignore`), with no dependencies. If you do not want pytest-queryguard
installed in production, move the exception out of the code instead:
`queryguard_ignore = ["relationship-load Book.author function=nightly_lines"]`.

A finding is left out only when every load it counts ran inside the block;
one load after the block reports it in full ([the `ignore()`
block](ignoring.md#the-ignore-block)). zeal instead drops the loads made
inside its block when the block exits (`zeal_ignore()` works on a copy of the
counts), so they never add to the loads after it.

### Unused ignores

zeal 2.3.0 checks each `zeal_ignore()` block when it exits, if
`ZEAL_CHECK_UNUSED_IGNORES = True` and detection was on: a block that
suppressed no N+1 raises `UnusedIgnoreError`, or warns with
`ZEAL_RAISE = False`. `ZEAL_ALLOWLIST` entries are not checked.

pytest-queryguard lists every `queryguard_ignore` pattern, marker and
`ignore()` block that left out nothing in the run, in a summary at the end,
with no setting:

```text
========================== queryguard: stale ignores ===========================
queryguard_ignore patterns that matched no finding in this run:
  relationship-load Author.books
Remove them if the finding is fixed, or fix a mistyped pattern.
```

It does not fail the run, and it is printed only after a run of the whole
suite that ran to the end (not with `-k`, a file argument, `--lf` or `-x`),
where zeal checks a block in any run that enters it.

### Signals and logging

zeal sends the Django signal `zeal.signals.nplusone_detected` (arguments
`sender` and `exception`) for each N+1 it reports, so a receiver can log it or
send it elsewhere. It defines a `zeal` logger in `listeners.py` but does not
log to it in 2.3.0: with `ZEAL_RAISE = False` it issues a `UserWarning`.

pytest-queryguard has no signal and no hook per finding. What it has instead:

- in warn mode, a `QueryGuardWarning` per finding, which `filterwarnings`
  can turn into an error or leave out
  ([Configuration](configuration.md));
- `--queryguard-json=PATH`, a file with each test's statement count and
  findings, for a script to read ([CI](ci.md#query-counts-as-json));
- `--queryguard-sarif=PATH` for GitHub code scanning ([SARIF](sarif.md)).

## Before and after

The models are the ones of [Django](django.md#getting-started) (`Author`, and
`Book` with a foreign key to it), in an app called `library`, with
`django.contrib.contenttypes` installed (zeal 2.3.0 imports it when it
patches generic relations, and fails at start-up without it). Two functions
with an N+1 each:

```python
# library/catalog.py
from library.models import Book


def book_lines():
    return [f"{book.title} by {book.author.name}" for book in Book.objects.all()]


def titles(ids):
    return [Book.objects.get(pk=pk).title for pk in ids]
```

and two tests that call them:

```python
# tests/test_books.py
import pytest

from library.catalog import book_lines, titles
from library.models import Author, Book


@pytest.fixture
def books(db):
    for i in range(5):
        author = Author.objects.create(name=f"author {i}")
        Book.objects.create(title=f"book {i}", author=author)


def test_book_lines(books):
    assert len(book_lines()) == 5


def test_titles(books):
    ids = list(Book.objects.values_list("pk", flat=True))
    assert len(titles(ids)) == 5
```

### Before

zeal in the settings, as its README sets it up for tests, and the fixture in
the root conftest:

```python
# settings.py (excerpt)
INSTALLED_APPS = ["django.contrib.contenttypes", "library", "zeal"]
ZEAL_ALLOWLIST = []
```

```python
# conftest.py
import pytest
from zeal import zeal_context


@pytest.fixture(autouse=True)
def use_zeal():
    with zeal_context():
        yield
```

Both tests fail, at the second load:

```text
E   zeal.errors.NPlusOneError: N+1 detected on library.Book.author at .../library/catalog.py:5 in book_lines
E   zeal.errors.NPlusOneError: N+1 detected on library.Book.get() at .../library/catalog.py:9 in titles
FAILED tests/test_books.py::test_book_lines - zeal.errors.NPlusOneError: N+1 ...
FAILED tests/test_books.py::test_titles - zeal.errors.NPlusOneError: N+1 dete...
2 failed
```

### After

```diff
 # settings.py
-INSTALLED_APPS = ["django.contrib.contenttypes", "library", "zeal"]
-ZEAL_ALLOWLIST = []
+INSTALLED_APPS = ["django.contrib.contenttypes", "library"]
```

```diff
 # conftest.py
-import pytest
-from zeal import zeal_context
-
-
-@pytest.fixture(autouse=True)
-def use_zeal():
-    with zeal_context():
-        yield
```

```diff
 # pyproject.toml
 [tool.pytest.ini_options]
 DJANGO_SETTINGS_MODULE = "settings"
+# while you migrate: queryguard_mode = "warn"
+# for the findings you keep: queryguard_baseline = "queryguard-baseline.json"
```

The same tests fail with the plugin's reports:

```text
queryguard: N+1 detected

  Book.author was lazy-loaded 5 times
  first at library/catalog.py:5 in book_lines
    called from tests/test_books.py:15 in test_book_lines
  parents loaded at library/catalog.py:5 in book_lines
  kind: relationship-load  label: Book.author
  docs: https://maciejziel.github.io/pytest-queryguard/how-detection-works.html#relationship-load
  sql: SELECT "library_author"."id", "library_author"."name" FROM "library_author" WHERE "library_author"."id" = %s LIMIT 21
  fix: load it with the parent query, e.g.
       Book.objects.select_related("author")
       or, on Django 6.1+, .fetch_mode(models.FETCH_PEERS) on that query
```

```text
queryguard: N+1 detected

  the same statement ran 5 times
  first at library/catalog.py:9 in titles
    called from tests/test_books.py:20 in test_titles
  kind: repeated-statement  label: SELECT "library_book"."id", "library_book"."title", "library_book"."author_id" FROM "library_book" WHERE "library_book"."id" = %s LIMIT ?
  docs: https://maciejziel.github.io/pytest-queryguard/how-detection-works.html#repeated-statement
  fix: fetch all rows in one query instead of in a loop, e.g.
       Model.objects.in_bulk(ids) or Model.objects.filter(pk__in=ids)
```

`Book.objects.select_related("author")` and `Book.objects.in_bulk(ids)` fix
them. To accept them instead, as a `ZEAL_ALLOWLIST` would:

```toml
[tool.pytest.ini_options]
queryguard_ignore = [
    "relationship-load Book.author",
    'repeated-statement SELECT * FROM "library_book" WHERE "library_book"."id" = *',
]
```

Both tests then pass.

## Step by step

1. **Install pytest-queryguard next to zeal.** Keep zeal as it is.

   ```bash
   pip install "pytest-queryguard[django]"
   ```

   It needs Django 5.2, 6.0 or 6.1 and pytest-django (or `QueryGuardRunner`
   for `manage.py test`).

2. **Run both side by side.** zeal raises at the second load, and the test
   stops there, so pytest-queryguard has no finished test to report on.
   Turn zeal's raising off for this run and put the plugin in warn mode:

   ```python
   ZEAL_RAISE = False  # in the test settings, while you compare
   ```

   ```console
   $ pytest --queryguard-mode=warn
   ```

   Both tools then warn on the same run. For the example above, zeal issued a
   `UserWarning` per load past its threshold (`N+1 detected on
   library.Book.author at .../library/catalog.py:5 in book_lines`, four
   times) and pytest-queryguard one `QueryGuardWarning` per finding:
   `2 passed, 10 warnings`. The two tools did not interfere in this run, with
   zeal's fixture or with its middleware and a test-client request. Compare
   the two lists: what zeal reports and the plugin does not is usually test
   code, `.get()` loops under 5 rows or deferred fields read for under 5
   objects (see [above](#the-main-differences-in-behaviour)).

3. **Translate the configuration** with the tables above:
   `ZEAL_ALLOWLIST` into `queryguard_ignore`, `zeal_ignore()` in tests into
   markers, the thresholds into the three options.

4. **Record a baseline** of the findings you cannot fix yet, on a full run
   (zeal's raising still off):

   ```console
   $ pytest --queryguard-baseline=queryguard-baseline.json --queryguard-update-baseline
   ...
   queryguard-baseline.json: 2 entries (2 added, 0 removed)
   ```

   then set `queryguard_baseline = "queryguard-baseline.json"` in the
   configuration and commit the file ([Baseline](baseline.md)). A finding in
   the file no longer fails; a new one does. Entries are keyed on kind,
   label, file and function, not on zeal's model and field.

5. **Remove zeal from the tests**: the autouse fixture or the unittest mixin,
   `"zeal"` in the test settings' `INSTALLED_APPS`, the middleware if it was
   there for the tests, the `ZEAL_*` settings, and the `zeal_ignore()` calls
   (replaced in step 3). If zeal stays for the development server or Celery,
   keep it there and only take it out of the test settings.

6. **Fail on findings**: remove `queryguard_mode = "warn"` (`fail` is the
   default) and run the suite in CI, with the [action](ci.md) or SARIF if
   you want the findings in pull requests.

7. **Prune** as you fix: the "stale ignores" summary names the patterns and
   blocks that no longer match, and a full update run removes fixed entries
   from the baseline.

## What each tool reports that the other does not

What pytest-queryguard has that zeal 2.3.0 does not (zeal's README
describes N+1 detection only):

- repeated statements beyond `.get()`: `filter(...).exists()`, `.count()` or
  raw SQL run once per item (`repeated-statement`);
- relation reads zeal missed in the [Django corpus run of
  2026-11-07](django.md#comparison) (zeal 2.3.0): reverse many-to-many reads,
  `count()`, `filter()` and `exists()` per row, `refresh_from_db()`, a
  serializer method that queries, and generic relations;
- opt-in checks: `write-loop` (`save()` or `create()` per row), the
  experimental `queryset-len`, and the [missing-index check](index-check.md)
  on SQLite, PostgreSQL and MySQL;
- query budgets: the `max_queries` marker, `queryguard_max_queries` for every
  test, and a [budget file](budgets.md#lock-todays-counts-the-budget-file);
- adoption and CI: a [baseline](baseline.md), `--queryguard-json`,
  [SARIF](sarif.md) and a [GitHub Action](ci.md);
- a report with the callers, the line that loaded the parents and the fix.

What zeal has that pytest-queryguard does not:

- it runs outside tests: in the running app with its middleware, in Celery
  workers, anywhere with `setup()` or `zeal_context()`;
- a Django signal per N+1, for receivers of your own;
- a check of `ZEAL_ALLOWLIST` names against the installed models;
- the unused-ignore check fails at the block, in any run, where the plugin
  lists stale ignores after full runs only;
- one threshold of 2 for every kind, so it reports smaller loops of `.get()`
  and deferred fields by default;
- less overhead: it records relation reads only, not every statement. On a
  300-test pytest-django suite, pytest-queryguard added 181 µs per test
  (+2.5 %, median) and zeal nothing measurable
  ([Overhead against the other tools](django.md#overhead-against-the-other-tools));
- no dependencies; pytest-queryguard needs pytest and SQLAlchemy installed.

On the real projects of the [Comparison](django.md#comparison) zeal
reported more than pytest-queryguard on every project (for Django REST
framework, 112 reports in 97 tests against 38 findings). Its largest groups
there were `sessions.Session.get()` once per request, where zeal counts the
requests of one test together, and a relation read in a model's `save()`;
pytest-queryguard counts each test-client request on its own and leaves out
loops of test code. Read that section before you take either count as the
number of real N+1s.
