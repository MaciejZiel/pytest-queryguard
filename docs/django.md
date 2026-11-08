# Django (experimental)

pytest-queryguard checks Django ORM code as it checks SQLAlchemy code: it
fails a test that runs an N+1, enforces query budgets, and writes the same
baseline, JSON and SARIF files. The Django support is **experimental** in this
release, outside the stability guarantees like the static check: its
findings and options may still change in a minor release (see
[Stability](stability.md#experimental)).

It is not related to the `django-query-guard` and `django-queryguard-n1`
packages on PyPI.

## Getting started

```bash
pip install "pytest-queryguard[django]" pytest-django
```

It needs Django 5.2, 6.0 or 6.1 and works next to
[pytest-django](https://pytest-django.readthedocs.io/): nothing goes into
`INSTALLED_APPS`, `MIDDLEWARE` or your settings, and there is nothing to
import. A project that already runs its tests with pytest-django is checked
from the next run on.

A model with a foreign key:

```python
# library/models.py
from django.db import models


class Author(models.Model):
    name = models.CharField(max_length=100)


class Book(models.Model):
    title = models.CharField(max_length=200)
    author = models.ForeignKey(Author, on_delete=models.CASCADE, related_name="books")
```

and a test whose code reads the author of each book:

```python
# tests/test_books.py
import pytest

from library.models import Author, Book


@pytest.fixture
def books(db):
    for i in range(3):
        author = Author.objects.create(name=f"author {i}")
        Book.objects.create(title=f"book {i}", author=author)


def book_lines():
    return [f"{book.title} by {book.author.name}" for book in Book.objects.all()]


def test_book_lines(books):
    assert len(book_lines()) == 3
```

The test fails, at the end of the test, with:

```text
queryguard: N+1 detected

  Book.author was lazy-loaded 3 times
  first at tests/test_books.py:15 in book_lines
  parents loaded at tests/test_books.py:15 in book_lines
  kind: relationship-load  label: Book.author
  docs: https://maciejziel.github.io/pytest-queryguard/how-detection-works.html#relationship-load
  sql: SELECT "library_author"."id", "library_author"."name" FROM "library_author" WHERE "library_author"."id" = %s LIMIT 21
  fix: load it with the parent query, e.g.
       Book.objects.select_related("author")
       or, on Django 6.1+, .fetch_mode(models.FETCH_PEERS) on that query
```

With `Book.objects.select_related("author")` the test passes. The fixture's
writes are not measured: only the test function's body is, as for SQLAlchemy.

## Without pytest (manage.py test)

A project that runs its tests with Django's own runner (`manage.py test`,
unittest `TestCase`s) gets the same detection from a test runner, with no
change to the tests. Two lines in the settings:

```text
TEST_RUNNER = "pytest_queryguard.django.QueryGuardRunner"
QUERYGUARD = {"mode": "fail"}  # optional: the default
```

```bash
pip install "pytest-queryguard[django]"
python manage.py test
```

`QueryGuardRunner` is Django's `DiscoverRunner` with each test measured as
the pytest plugin measures it: `setUp()`, the test method, `tearDown()` and
the cleanups. `setUpClass()`, `setUpTestData()` and Django's own database
set-up are not measured, as pytest fixtures are not. With the models above,
a helper that reads the author of each book:

```python
# library/listing.py
from library.models import Book


def book_lines():
    return [f"{book.title} by {book.author.name}" for book in Book.objects.all()]
```

and a test case that calls it:

```python
# library/tests.py
from django.test import TestCase

from library.listing import book_lines
from library.models import Author, Book


class BookTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for i in range(3):
            author = Author.objects.create(name=f"author {i}")
            Book.objects.create(title=f"book {i}", author=author)

    def test_book_lines(self):
        self.assertEqual(len(book_lines()), 3)
```

the test fails with the plugin's report:

```text
FAIL: test_book_lines (library.tests.BookTests.test_book_lines)
----------------------------------------------------------------------
AssertionError: queryguard: N+1 detected

  Book.author was lazy-loaded 3 times
  first at library/listing.py:6 in book_lines
    called from library/tests.py:16 in test_book_lines
  parents loaded at library/listing.py:6 in book_lines
  kind: relationship-load  label: Book.author
  docs: https://maciejziel.github.io/pytest-queryguard/how-detection-works.html#relationship-load
  sql: SELECT "library_author"."id", "library_author"."name" FROM "library_author" WHERE "library_author"."id" = %s LIMIT 21
  fix: load it with the parent query, e.g.
       Book.objects.select_related("author")
       or, on Django 6.1+, .fetch_mode(models.FETCH_PEERS) on that query
```

A test that fails on its own, or is skipped, is reported as it is, without
the analysis. A project with a runner of its own subclasses
`QueryGuardRunner` instead of `DiscoverRunner`. To check only some test
cases, under any runner, mix `QueryGuardTestCase` in instead:

```python
from django.test import TestCase
from pytest_queryguard.django import QueryGuardTestCase


class BookTests(QueryGuardTestCase, TestCase):
    ...
```

Under pytest the mixin does nothing: the plugin measures every test already.

**Settings.** `QUERYGUARD` takes the names of the
[ini options](configuration.md) without the `queryguard_` prefix, with the
same defaults and values: `mode` (`fail`, `warn`, `off`),
`relationship_threshold`, `repeat_threshold`, `small_repeat_threshold`,
`column_load_threshold`, `write_loop_threshold`, `scope`, `test_code`,
`fail_on`, `ignore` (a list of [ignore patterns](ignoring.md)),
`max_events` and `stack_depth`. `python manage.py test --queryguard-mode=warn`
overrides `mode`; the command line wins over `QUERYGUARD`, which wins over
the defaults. `pytest.ini` and `pyproject.toml` are not read. An unknown name
or a bad value stops the run with `ImproperlyConfigured` before the test
database is created.

**Warn mode** issues each report as a `QueryGuardWarning` at the test
method, shown after the test (also with `--buffer`).
`python -W "error:queryguard:UserWarning" manage.py test` turns them into
test errors (the message starts with `queryguard:`; `-W` cannot name
`QueryGuardWarning`, which is not imported when Python starts).

**Test code** is the modules of the test cases the runner loaded, the files
below a `tests` or `test` folder of the current directory, and factories, as
under pytest: with the default `test_code = off` a loop in the test method or
`setUp()` is not reported, a loop in a view, serializer or model method it
calls is. `ignore()` blocks work as under pytest.

**`--parallel`** works, with `fork`, `spawn` and `forkserver` workers: each
worker measures its tests and the failures come back as any test failure;
warnings are printed by the worker.

**Not available without pytest** (yet): the `max_queries` marker and the
budget file (the `max_queries()` context manager works), the
baseline, `--queryguard-json`, SARIF, the end-of-run summary, strict mode and
the index check. They need pytest-django and the plugin. Like the rest of the
Django support, the runner is experimental; see the
[design note](design-django-runner.md).

## What is checked

| Kind | Django code it reports | Fix the message names |
|---|---|---|
| `relationship-load` | a foreign key, a one-to-one relation (either side, multi-table inheritance included), a reverse foreign key, a many-to-many relation, a generic foreign key or a `GenericRelation` read for 2 or more objects of one query line; also `.filter()`, `.count()`, `.exists()` and `.values()` on such a relation | `select_related()` or `prefetch_related()` with the lookup, through a chain (`"author__publisher"`) |
| `column-load` | a field left out by `.only()` or `.defer()` read for 5 or more objects | name it in `.only()`, or leave it out of `.defer()` |
| `repeated-statement` | one SELECT shape run 5 or more times: `get()`, `filter(...).exists()`, `refresh_from_db()` or a serializer method that queries, per item | `in_bulk(ids)` or `filter(pk__in=ids)` |
| `missing-index` (off by default) | a SELECT that no index serves, on SQLite, PostgreSQL (psycopg 2 or 3) and MySQL; see [Index check](index-check.md) | the index to create |
| `write-loop` (off by default) | one INSERT, UPDATE or DELETE per row: `save()`, `create()`, `delete()` in a loop | `bulk_create()`, `bulk_update()`, `queryset.update()`, `filter(pk__in=ids).delete()` |

The rules, thresholds, options, markers and files are the ones described in
[How detection works](how-detection-works.md) and the
[Configuration reference](configuration.md); this page only lists what is
particular to Django.

Not reported:

- Loads that `prefetch_related()`, `select_related()` or a `Prefetch` object
  answered; lookups of single objects at different lines (`Book.objects.get(pk=1).author`
  and, at another line, `Book.objects.get(pk=2).author`); a NULL foreign key.
- Django 6.1 fetch modes: a query with `.fetch_mode(models.FETCH_PEERS)` loads
  the relation of all its objects at once, which is no N+1.
- A delete with its cascade (`author.delete()` deleting the books and their
  reviews), and the batches of one `bulk_create()` or `bulk_update()`: each
  is one unit of work for the `write-loop` rule.
- The `SAVEPOINT` and `RELEASE SAVEPOINT` statements of `transaction.atomic()`
  never repeat; budgets count them, as `assertNumQueries` does.
- The statements Django runs to set up a new connection (on MySQL, the `SET`
  of the isolation level) are not counted, as `assertNumQueries` does not
  count them: a test that opens the connection, after a `TestCase` class
  closed it, counts the same as one that does not.

Everything that works from these kinds works for Django: budgets
(`max_queries`, the `queryguard` fixture, the budget file), `record_queries()`,
`ignore()` blocks, the `queryguard_ignore` marker and option,
`--queryguard-index-check` with the index advisor, the
[baseline](baseline.md), `--queryguard-json` with the pull-request comparison
and the [GitHub Action](ci.md), [SARIF](sarif.md) and strict mode
(`queryguard_strict`, which raises `NPlusOneDetected` at the attribute access
that makes the N+1).

Each request of the test client (one call of `client.get()`; a view called
directly with `RequestFactory` is no request) always counts on its own, also
with the default `queryguard_scope = test`: a test that calls a view once per
object makes separate requests, each looking up its session and user once,
and that is no loop of the application. The rest of the test counts as a
whole. With `queryguard_scope = session`, statements outside a request also
count per database connection, as a Session does for SQLAlchemy. Requests of
the `AsyncClient` count per connection before Django 6.1. See
[Several sessions or requests](several-sessions.md).

The N+1s of test code are left out by default, as for SQLAlchemy
(`queryguard_test_code`, see [Test code](how-detection-works.md#test-code)):
a loop in the test function, a fixture or a factory is not reported, a loop
in a view, serializer or model method the test calls is. A call of the async
ORM, which runs in a thread of asgiref with no line of the test on its stack,
always counts as the application's.

## Collecting queries without failing the test

pytest-django's `django_assert_num_queries` checks the count when its block
ends, so a wrong count fails the test before the test's own assertions run.
[pytest-django#1034](https://github.com/pytest-dev/pytest-django/issues/1034)
asks for a way to see the queries without that failure, for example while
debugging why a test that also checks the logic now runs one query fewer.
pytest-django's maintainer declined it as too niche and pointed to Django's
`CaptureQueriesContext`. With pytest-queryguard there are several ways, none
of them new API.

`record_queries()` records the block and fails nothing; the test decides
when to look at the count. Here the logic is checked first, then the count, and
a wrong count prints the statements:

```python
# library/services.py
from library.models import Author, Book


def add_book(title, author_name):
    author = Author.objects.create(name=author_name)
    return Book.objects.create(title=title, author=author)
```

```python
# tests/test_add_book.py
from library.models import Book
from library.services import add_book
from pytest_queryguard import record_queries


def test_add_book(db):
    with record_queries() as log:
        add_book("Dune", "Frank Herbert")

    book = Book.objects.get()
    assert book.author.name == "Frank Herbert"
    assert log.count == 2, [query.sql for query in log]
```

The other ways:

- `@pytest.mark.max_queries(n)` is checked after the test body, and when the
  body fails, its failure is reported, not the budget. It fails only above
  `n`, not below (see [Budgets and timing](budgets.md)).
- `--queryguard-mode=warn` (or `queryguard_mode = warn`) turns N+1 findings
  into `QueryGuardWarning`s, and the test passes. Budgets are not findings:
  `max_queries`, `queryguard.num_queries()` and the budget file fail in every
  mode.
- `--queryguard-report` prints the tests with the most queries at the end of
  the run, and `--queryguard-json=PATH` writes every test's count to a file
  (see [CI](ci.md#query-counts-as-json)); neither fails a test.

## How it sees Django

- **Statements** come from Django's documented
  [`connection.execute_wrapper`](https://docs.djangoproject.com/en/stable/topics/db/instrumentation/).
  The plugin puts its wrapper first in the list of every connection, so a
  wrapper of your own still runs and is still removed by its own
  `with connection.execute_wrapper(...)` block. Raw SQL through
  `connection.cursor()` is measured too.
- **Lazy loads** are seen through a few class-level hooks, installed once
  Django's settings are configured and its apps are loaded: the descriptors of
  foreign keys and reverse one-to-one relations, the managers of reverse
  foreign keys and many-to-many relations, and the descriptor of deferred
  fields. Querysets and model instances still pickle and copy as before: the
  plugin attaches nothing to them that would not.
- **Where the parents came from**: the plugin notes the query line of each
  instance a queryset builds, so lazy loads are grouped by that line as for
  SQLAlchemy (see [How detection works](how-detection-works.md#relationship-load)).
  The objects `select_related()` builds with each row count as loaded there
  too, so a load below them names the whole lookup
  (`select_related("author__publisher")`).
- **Call sites** skip the frames of Django, asgiref and Django REST framework,
  as they skip SQLAlchemy's: a finding points at your view, serializer or test.
  A DRF view whose queryset is a class attribute has no line of its own: the
  finding points at the test line that called the view, and the fix belongs in
  the view's `queryset` or `get_queryset()`. A call of the async ORM
  (`await Book.objects.aget(...)`, `async for`), which Django runs in a
  thread of asgiref, has the line that awaited it as its call site.

## Limitations

- **Values Django reloads by itself**: on a database without `RETURNING`
  (MySQL), a field saved as an expression (`F("pages") + 1`) is reloaded on
  its next read; such reads in a loop are reported as a `column-load` whose
  fix names `.only()`. Generated fields are left out.
- **Durations** include the time of other `execute_wrapper`s, since the
  plugin's wrapper is the outermost.
- Not for Django yet: `unused-eager-load`, `unused-columns`, `cartesian-load`,
  `--queryguard-write-patch` and `--queryguard-show-patch` (Django findings
  have no patch), the static check and the in-application mode.

## Accuracy

The Django corpus in `tests/django_project/corpus` has 73 scenarios, 37 with
an N+1 or another finding and 36 without, run in five configurations:
foreign keys, reverse relations, many-to-many relations, one-to-one relations,
generic relations,
multi-table inheritance, `select_related()`, `prefetch_related()` and
`Prefetch` objects, `.only()` and `.defer()`, Django REST framework
serializers, admin list, change and delete pages, templates, `get()` and
`exists()` per item, `save()` and `delete()` in a loop against bulk writes and
cascades, signals, `iterator()`, pagination, helpers called for objects the
test created, and requests of the test client. On SQLite, with Django 5.2 and
6.1, in the default configuration:

| Kind | Precision | Recall |
|---|---|---|
| `relationship-load` | 100 % (22 of 22) | 100 % |
| `column-load` | 100 % (5 of 5) | 100 % |
| `repeated-statement` | 100 % (7 of 7) | 100 % |
| `write-loop` (threshold 2) | 100 % (7 of 7) | 100 % |

The same holds in the other four configurations (small repeats, a column-load
threshold of 2, write loops, strict mode) and on PostgreSQL 17 and MySQL 8.4,
except for one scenario with two N+1s in strict mode: the test stops at the
first, before the second is complete. `scripts/django_accuracy_report.py`
prints them.

**These numbers are biased.** We wrote the corpus, together with the
integration: it says that the cases it names work, not how often the rules
are right on other code. Five of its scenarios (`clean_35`, `clean_36`,
`bug_35` to `bug_37`) were added after the runs on real projects below found
false positives, each before its fix. For an estimate on code we did not
write, see [Real projects](#real-projects).

## Real projects

The test suites of six open-source Django projects ran in GitHub Actions on
2026-11-07, each once without the plugin, once with it in warn mode, and once
under each of [django-zeal](#comparison) and nplusone (the `django` suite of
`.github/workflows/realworld.yml`; projects, tags and commands in
`scripts/realworld_django_projects.json`). Final run:
[42420546251](https://github.com/MaciejZiel/pytest-queryguard/actions/runs/42420546251);
the first one, before the fixes below:
[42416001785](https://github.com/MaciejZiel/pytest-queryguard/actions/runs/42416001785).

| Project | Tests run | Passed / failed / skipped without the plugin | Change with the plugin | Internal warnings / errors | Findings | Overhead, two runs |
|---|---|---|---|---|---|---|
| Django REST framework 3.18.3 (Django 6.1) | all | 1655 / 0 / 9 | none | 0 / 0 | 38: 32 relationship-load, 6 repeated-statement | −5.7 %, −2.9 % (a 6 s suite) |
| Read the Docs 2026.11.03 | api, projects, builds, organizations | 585 / 0 / 5 | none | 0 / 0 | 45: 15 relationship-load, 28 repeated-statement, 2 column-load | +1.7 %, −0.3 % |
| django-cms 5.1.3 | all | 1781 / 1 / 70 | none | 0 / 0 | 2539: 521 relationship-load, 2017 repeated-statement, 1 column-load | +15.9 %, +8.6 % |
| pretix 2026.9.0 | `tests/api` | 1654 / 1 / 0 | none | 0 / 0 | 378: 124 relationship-load, 254 repeated-statement | +2.2 %, −2.0 % |
| saleor 3.23.47 (PostgreSQL) | the query-count benchmarks and product queries | 1487 / 0 / 0 | none | 0 / 0 | 258: 233 relationship-load, 25 repeated-statement | −1.4 %, −2.9 % |
| django-oscar 4.2.1 (PostgreSQL) | functional and integration | 1697 / 0 / 0 | none | 0 / 0 | 641: 278 relationship-load, 363 repeated-statement | +3.7 %, +8.4 % |

The plugin changed no test's outcome and reported no internal error in any of
the 8,945 tests, in either run. The overheads are of single runs (the
first before SQLAlchemy was imported lazily, the second in
[run 42422111060](https://github.com/MaciejZiel/pytest-queryguard/actions/runs/42422111060)
with the final code), and the runs without any tool vary by up to 30 %
between runs of the workflow, so only django-cms stands out: it runs about 90
statements in each test body, and the cost per statement (about 15 µs on a
laptop: finding the call site and recording the statement) shows there.
django-zeal and nplusone added between −8 % and +3 % in the same runs.

**Triage.** All 38 findings of Django REST framework and 12 distinct finding
sites drawn at random from each other project (98 in all) were read in the
projects' code:

| Verdict | Count | What they are |
|---|---|---|
| true | 68 | a relation or the same query per item in a loop of the application: serializers and GraphQL resolvers reading a relation per row, signal receivers per saved row, tree walks, per-row deletes |
| debatable | 28 | the same query at several lines of straight test code (fixtures made one by one, assertions), a loop of the test over cases or pages, settings looked up again and again in one request |
| false | 2 | django-oscar's concurrency test: five threads, each placing one order on its own connection, counted together |

The first run had 5,071 findings in the five projects that ran (django-oscar
did not: it needs PostgreSQL); the same five have 3,258 now. The false
positives it showed, each fixed with a scenario added first:

- **A test that makes several requests** (Read the Docs, pretix, django-cms,
  DRF): every request looks up its session, user, token or settings once, and
  those lookups were counted together. Each request of the test client now
  counts on its own (`clean_36`, `bug_37`); pretix went from 861 to 254
  `repeated-statement` findings.
- **One helper, two objects the test created** (Read the Docs: `Project.save()`
  reads `self.versions` of a project made in `setUp` and one made in the
  test, 210 findings; saleor 85; django-cms about 170): loads of objects of
  unknown origin are now grouped by the call path of the read, not by its
  line alone (`clean_35`, `bug_35`, `bug_36`).
- **Django's own test-case machinery** (DRF, django-cms): the flush and
  `post_migrate` of `TransactionTestCase` tear-downs ran content type and
  permission queries inside the measured call; nothing is recorded in
  `_pre_setup` and `_post_teardown` now (a test in `tests/django_project`;
  the corpus cannot hold a `TransactionTestCase`).

Not fixed: work spread over threads (the two false findings above) still
counts together with `queryguard_scope = test`; `queryguard_scope = session`
counts each connection on its own.

## Comparison

The Django corpus under each tool, in GitHub Actions on 2026-11-07
([run 42421653867](https://github.com/MaciejZiel/pytest-queryguard/actions/runs/42421653867);
`scripts/head_to_head/django_corpus.py`, `head-to-head.yml` with
`suite=django`). Each tool as its documentation says to make it fail a test:
django-zeal 2.3.0 in `INSTALLED_APPS` with `zeal_context()` around each test
call (it raises at the first N+1); nplusone 1.0.0 in `INSTALLED_APPS` with its
`Profiler`. Django 5.2, SQLite:

| Tool | Precision | Recall, all BUG | `relationship-load` P / R | `repeated-statement` P / R | `column-load` P / R |
|---|---|---|---|---|---|
| pytest-queryguard, defaults | 100 % | 89 % (`write-loop` is off by default) | 100 / 100 % | 100 / 100 % | 100 / 100 % |
| pytest-queryguard, write loops on | 100 % | 100 % | 100 / 100 % | 100 / 100 % | 100 / 100 % |
| django-zeal 2.3.0 | 93 % | 70 % | 90 / 86 % | 67 / 29 % | 100 / 80 % |
| nplusone 1.0.0 | 84 % | 43 % | 100 / 68 % | n/a | n/a |

- django-zeal misses reverse many-to-many reads, `count()`, `filter()` and
  `exists()` per row, `refresh_from_db()`, a serializer method that queries
  and generic relations; it flags `clean_35` and `clean_36`, the two cases our
  own first real-project runs got wrong. Its `get()` check is our
  `repeated-statement` for `get()` only.
- nplusone reports an unused eager load in three CLEAN scenarios (a kind the
  corpus does not score for Django, so each counts against it) and does not
  report `.count()`, generic relations or the deferred-field loads. On Django
  6.1 it crashes (`ValueError: too many values to unpack`) in every query with
  `select_related()`: 8 scenarios there, and 15 tests of Django REST
  framework.
- The bias above holds here too, and more: the corpus was written by us, for
  this plugin's rules. The kinds a tool does not claim are left out of its
  per-kind columns, but its overall recall counts every BUG scenario.

What each tool reports on the real projects (final run above; reports counted
once per test and message):

| Project | pytest-queryguard findings | django-zeal reports (tests) | nplusone reports (tests) |
|---|---|---|---|
| Django REST framework | 38 | 112 (97) | 36 (36), and 15 tests it broke |
| Read the Docs | 45 | 1445 (430) | 225 (138) |
| django-cms | 2539 | 2704 (980) | 768 (512) |
| pretix | 378 | 1479 (515) | 2756 (771) |
| saleor | 258 | 461 (292) | 1615 (1195) |
| django-oscar | 641 | 2875 (863) | 986 (495) |

django-zeal's largest groups are the patterns fixed above:
`sessions.Session.get()` once per request (DRF, Read the Docs) and
`Project.versions` read by `Project.save()` (Read the Docs, 225 times).
nplusone's are mostly unused eager loads (`OrderPosition.checkins`, 294 times
in pretix).

### Overhead against the other tools

A 300-test pytest-django suite of the test project (each test reads 18 books
with `select_related()`, gets three books and counts them; no N+1), Django
5.2, 9 interleaved rounds on one runner (`scripts/head_to_head/django_overhead.py`,
same run), against the same suite with no tool:

| | pytest-queryguard | django-zeal | nplusone |
|---|---|---|---|
| added wall time, best / median round | +1.3 % / +3.0 % | +2.0 % / −0.5 % | +6.0 % / +2.4 % |
| fixed cost per pytest run (median) | +28 ms | −12 ms (noise) | −9 ms (noise) |
| added per test (median) | +181 µs (+2.5 %) | −7 µs (noise) | +247 µs (+3.5 %) |

Before the work on this
([run 42418336858](https://github.com/MaciejZiel/pytest-queryguard/actions/runs/42418336858))
the plugin added 10 % to that suite: 278 ms per run, nearly all of it importing
SQLAlchemy in a project that does not use it, and 722 µs per test. SQLAlchemy
is now imported only once the project imports it; between tests the plugin's
execute wrapper is off; and the hooks return at once while nothing is
measured. django-zeal records no statements, only relation reads, so it has
less to do per statement; pytest-queryguard records every statement for the
budgets and the `repeated-statement` rule.

## Overhead

`scripts/bench_django_overhead.py` times three workloads in-memory on SQLite
with Django 6.1 and Python 3.15, each in a fresh process: one `get()` per
statement, iterating books with `select_related()` (the cost per instance)
and reading `book.author` of each book (a lazy load each). Median of 7 runs
on a laptop, in microseconds per operation:

| Workload | Not installed | Installed, not measuring | Measuring |
|---|---|---|---|
| `get()` | 129 | 134 (+4 %) | 157 (+21 %) |
| a row with `select_related()` | 6.8 | 6.8 | 7.4 (+9 %) |
| a lazy load | 155 | 156 (+1 %) | 197 (+28 %) |

The cost is per statement and per lazy load (finding the call site, recording
the event). On a test suite, where statements are a part of each test's time,
the share is lower: see the suite above and the real projects.
