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

a function that reads the author of each book:

```python
# library/catalog.py
from library.models import Book


def book_lines():
    return [f"{book.title} by {book.author.name}" for book in Book.objects.all()]
```

and a test that calls it:

```python
# tests/test_books.py
import pytest

from library.catalog import book_lines
from library.models import Author, Book


@pytest.fixture
def books(db):
    for i in range(3):
        author = Author.objects.create(name=f"author {i}")
        Book.objects.create(title=f"book {i}", author=author)


def test_book_lines(books):
    assert len(book_lines()) == 3
```

The test fails, at the end of the test, with:

```text
queryguard: N+1 detected

  Book.author was lazy-loaded 3 times
  first at library/catalog.py:6 in book_lines
    called from tests/test_books.py:16 in test_book_lines
  parents loaded at library/catalog.py:6 in book_lines
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
`QueryGuardRunner` instead of `DiscoverRunner`; a `test_runner` that picks
its own result class (xmlrunner's `XMLTestRunner`) keeps it, and the tests
are measured all the same. To check only some test
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
database is created (with the mixin alone, at the first test it measures).

**Warn mode** issues each report as a `QueryGuardWarning` at the test
method, shown after the test (also with `--buffer`). Django sends warnings
to the `py.warnings` logger: with the default `LOGGING` they reach stderr; a
`LOGGING` of your own decides where they go.
`python -W "error:queryguard:UserWarning" manage.py test` turns them into
test errors (the message starts with `queryguard:`; `-W` cannot name
`QueryGuardWarning`, which is not imported when Python starts).

**Test code** is the modules of the test cases the runner loaded, the files
below a `tests` or `test` folder of the current directory, and factories, as
under pytest: with the default `test_code = off` a loop in the test method or
`setUp()` is not reported, nor is a query that a loop of the test makes the
application run once per pass ([Loops of the test](how-detection-works.md#loops-of-the-test));
a loop in a view, serializer or model method it calls is. `ignore()` blocks work as under pytest.

**`--parallel`** works, with `fork`, `spawn` and `forkserver` workers: each
worker measures its tests and the failures come back as any test failure;
warnings are printed by the worker. A `QueryGuardRunner` subclass with a
`parallel_test_suite` of its own must subclass `QueryGuardParallelTestSuite`;
otherwise the run stops with `ImproperlyConfigured`, as its workers would
measure nothing.

**Not available without pytest** (yet): the `max_queries` marker and the
budget file (the `max_queries()` context manager works), the
baseline, `--queryguard-json`, SARIF, the end-of-run summary, strict mode and
the index check. They need pytest-django and the plugin. Like the rest of the
Django support, the runner (new in 1.1.0) is
[experimental](stability.md#experimental): its settings and reports may change
in a minor release. See the [design note](design-django-runner.md).

## What is checked

| Kind | Django code it reports | Fix the message names |
|---|---|---|
| `relationship-load` | a foreign key, a one-to-one relation (either side, multi-table inheritance included), a reverse foreign key, a many-to-many relation, a generic foreign key or a `GenericRelation` read for 2 or more objects of one query line; also `.filter()`, `.count()`, `.exists()` and `.values()` on such a relation | `select_related()` or `prefetch_related()` with the lookup, through a chain (`"author__publisher"`) |
| `column-load` | a field left out by `.only()` or `.defer()` read for 5 or more objects | name it in `.only()`, or leave it out of `.defer()` |
| `repeated-statement` | one SELECT shape run 5 or more times: `get()`, `filter(...).exists()`, `refresh_from_db()` or a serializer method that queries, per item; also through one object's relation (`author.books.get(pk=pk)` per item) | `in_bulk(ids)` or `filter(pk__in=ids)` |
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
the `AsyncClient` (and of an ASGI handler) count on their own too, on every
supported Django version, and so do the requests of the live server
(`live_server`, `LiveServerTestCase`), each in a thread of the server. A
thread that runs test code (its target is a function of the test: a helper
that runs a function in five threads to test a race) is a unit of its own as
well, on its connection; a thread whose target is a function of the
application is not, also when the test started it
(`Thread(target=place_order)`). A streamed body (`StreamingHttpResponse`,
`FileResponse`) counts in its request when the test reads it, also after
other requests. See
[Several sessions or requests](several-sessions.md).

The N+1s of test code are left out by default, as for SQLAlchemy
(`queryguard_test_code`, see [Test code](how-detection-works.md#test-code)):
a loop in the test function, a fixture or a factory is not reported, nor is
a query that a loop of the test makes the application run once per pass
(objects created one by one whose `post_save` receiver reads a relation, a
helper called per case; see [Loops of the test](how-detection-works.md#loops-of-the-test)).
A loop in a view, serializer or model method the test calls is. So is a loop in a
view that runs no line of your project, through the test client or called
directly: a generic view with a class-level `queryset`, its nested serializer
or its template, the admin. A serializer or template the test uses itself
(to build the expected data) is test code. A call of the async ORM, which runs in a thread of asgiref with no
line of the test on its stack, always counts as the application's.

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
  A view that runs no line of your project between Django and the query (a
  DRF generic view with a class-level `queryset` and a `ModelSerializer`, a
  `TemplateResponse` rendered after the view returned) is named by its class
  or function, with the test line that made the request as its caller: the
  fix belongs in the view's `queryset` or `get_queryset()`. A middleware of
  your project on the stack is passed over. A view of Django's own (a
  `ListView` set up in the URLconf) points at the test line. A call of the async ORM
  (`await Book.objects.aget(...)`, `async for`), which Django runs in a
  thread of asgiref, has the line that awaited it as its call site.

## Limitations

- **Values Django reloads by itself**: on a database without `RETURNING`
  (MySQL), a field saved as an expression (`F("pages") + 1`) is reloaded on
  its next read; such reads in a loop are reported as a `column-load` whose
  fix names `.only()`. Generated fields are left out.
- **Durations** include the time of other `execute_wrapper`s, since the
  plugin's wrapper is the outermost.
- Some features are SQLAlchemy only by decision: see
  [Out of scope for Django](#out-of-scope-for-django).

## Out of scope for Django

These features of the plugin are not offered for Django code, and are not
planned. Each is a decision about scope, not a gap that the Django support
leaving experimental depends on. They find nothing in Django code; with
SQLAlchemy in the same project they work for its code as usual.

| Feature | Why not for Django |
|---|---|
| `unused-eager-load` (`queryguard_unused_eager_load`) | A `prefetch_related()` or `select_related()` whose objects the test never reads. Telling that apart needs a hook on every read of Django's prefetch caches and of each `select_related()` object, and it is wrong where a template or serializer reads them only for some rows: nplusone reports it and flags three of the CLEAN scenarios of the Django corpus. Its cost is one extra query, not one per row. |
| `unused-columns` (`queryguard_unused_columns`) | Columns loaded and never read. For Django it would mean watching every field read of every instance; the gain is a narrower `SELECT`, which `.only()` gives where it matters. `column-load` covers the harmful direction: a field left out and then read per row. |
| `cartesian-load` (`queryguard_cartesian_load`) | The rows multiplied by eager-loading two collections in one `JOIN`. `select_related()` follows only foreign keys and one-to-one relations, and `prefetch_related()` runs a query per relation, so the Django ORM does not build such a query from loader options. |
| Fix patches (`--queryguard-write-patch`, `--queryguard-show-patch`) | A patch adds a loader option at the line of the parents' query. In Django that query is often a class attribute (`queryset = Book.objects.all()` of a generic view), a manager method or a chain built over several lines, so a patch would be wrong as often as right. Each message names the code to add (`select_related("author")`). |
| The static check (`python -m pytest_queryguard.lint`) | It reads SQLAlchemy relationships from the source. For Django it would have to resolve models, managers and querysets across files, which a test run sees directly. |
| The in-application mode (`pytest_queryguard.app`) | It watches a SQLAlchemy engine in a running application. Django has django-debug-toolbar and django-silk for that; the plugin checks Django code in its tests. |

`QueryGuardRunner` (`manage.py test`) has no baseline, JSON, SARIF, budget
files, strict mode or index check yet: that is a gap of the runner, not of
the Django support, see
[Without pytest](#without-pytest-managepy-test).

## Accuracy

The Django corpus in `tests/django_project/corpus` has 75 scenarios, 38 with
an N+1 or another finding and 37 without, run in five configurations:
foreign keys, reverse relations, many-to-many relations, one-to-one relations,
generic relations,
multi-table inheritance, `select_related()`, `prefetch_related()` and
`Prefetch` objects, `.only()` and `.defer()`, Django REST framework
serializers, admin list, change and delete pages, templates, `get()` and
`exists()` per item, `save()` and `delete()` in a loop against bulk writes and
cascades, signals, `iterator()`, pagination, helpers called for objects the
test created, lookup helpers called from two lines, and requests of the test client. On SQLite, with Django 5.2 and
6.1, in the default configuration:

| Kind | Precision | Recall |
|---|---|---|
| `relationship-load` | 100 % (23 of 23) | 100 % |
| `column-load` | 100 % (5 of 5) | 100 % |
| `repeated-statement` | 100 % (8 of 8) | 100 % |
| `write-loop` (threshold 2) | 100 % (7 of 7) | 100 % |

The same holds in the other four configurations (small repeats, a column-load
threshold of 2, write loops, strict mode) and on PostgreSQL 17 and MySQL 8.4,
except for two scenarios with two findings each in strict mode: the test stops at the
first, before the second is complete. `scripts/django_accuracy_report.py`
prints them.

**These numbers are biased.** We wrote the corpus, together with the
integration: it says that the cases it names work, not how often the rules
are right on other code. Seven of its scenarios (`clean_35` to `clean_37`,
`bug_35` to `bug_38`) were added after the runs on real projects below found
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

Fixed since: work spread over threads (the two false findings above) counted
together with `queryguard_scope = test`. A thread that test code starts (its
target is test code, as `oscar.test.utils.run_concurrently` is) has a
connection of its own, and its statements now count on their own, as a
request does. A thread that the application starts, one per item, is still
counted with the rest of the test.

### Five more projects (2026-11-14)

A second run took five more projects, chosen for having pytest-django suites
that the first run did not cover: a document manager with a DRF API
(paperless-ngx), a recipe manager with a DRF API (Tandoor), an authentication
library (django-allauth), a GraphQL library with sync and async test clients
(strawberry-django) and a labelling tool (Label Studio). All ran on Django
5.2 and SQLite. Same workflow and settings as above. The final run, with the
two fixes below:
[43599524757](https://github.com/MaciejZiel/pytest-queryguard/actions/runs/43599524757).
The first runs, before them:
[43587676195](https://github.com/MaciejZiel/pytest-queryguard/actions/runs/43587676195)
and [43588110966](https://github.com/MaciejZiel/pytest-queryguard/actions/runs/43588110966).

| Project | Tests run | Passed / failed / skipped without the plugin | Change with the plugin | Internal warnings / errors | Findings (first run → final) | Overhead, two runs |
|---|---|---|---|---|---|---|
| paperless-ngx 3.4.2 | the API and workflow tests of `src/documents` | 713 / 0 / 0 | none | 0 / 0 | 167 → 167: 138 relationship-load, 29 repeated-statement | +0.6 %, −1.7 % |
| Tandoor 2.7.2 | `cookbook/tests/api` | 677 / 7 / 0 | none in the final run (see below) | 0 / 0 | 243 → 243: 216 relationship-load, 27 repeated-statement | 0.0 %, −0.9 % |
| django-allauth 65.22.1 | all | 2500 / 0 / 1 | none | 0 / 0 | 54 → 51: 2 relationship-load, 49 repeated-statement | +2.2 %, +6.9 % |
| strawberry-django 0.96.3 | all | 1278 / 0 / 69 | none | 0 / 0 | 131 → 97: 96 relationship-load, 1 repeated-statement | −0.6 %, −3.3 % |
| Label Studio 1.23.2 | `data_manager`, `projects`, `tasks`, `organizations`, `users` and `tests/data_manager` | 222 / 0 / 11 | none | 0 / 0 | 297 → 243: 163 relationship-load, 80 repeated-statement | −6.9 %, −6.0 % |

In all 5,478 tests, in both runs, the plugin changed no test's outcome and
reported no internal warning or error. The exception is one test of Tandoor
in the first run: `test_inherit[...-supermarket_category-False-cat_1]`
failed with the plugin only. That test makes its categories with
`get_or_create` on a random `faker.word()` name, so two of them can come out
as one. It passed in every later run and in the runs under django-zeal and
nplusone. Tandoor's seven failures without the plugin depend on the date and
on the order of the tests; they fail the same way with it.

**Triage.** 51 distinct finding sites of the first runs were read in the
projects' code: all 3 of django-allauth's, and 12 drawn at random from each
other project.

| Verdict | Count | What they are |
|---|---|---|
| true | 37 | DRF serializers and GraphQL resolvers reading a relation per row (most of paperless-ngx, Tandoor and strawberry-django), per-row work in signal handlers and delete actions, a migration that reads the filters of each group |
| debatable | 12 | 11 where test code drives the repetition: a test that loops over cases or calls one function ten times, fixtures and factories that create objects one by one, assertions per object. And one query that runs six times in one request (django-allauth's `is_mfa_enabled`), which could be cached but is not a loop |
| false | 2 | strawberry-django: `User.user_permissions` of the user of each async request, counted together; django-allauth: `UserSession.user` read once for each of the two sessions of one request |

Both false positives are fixed, each reproduced first:

- **Requests of the `AsyncClient` on Django 5.2 and 6.0**: before Django 6.1,
  `Signal.asend` runs the receivers of `request_started` in a task of their
  own, so the scope of each request was lost and a test's requests counted
  together. Async handlers now open the request scope themselves, and that
  includes a streamed body that the test reads after the call has returned.
  strawberry-django went from 131 to 97 findings: 30 of the
  `User.user_permissions` findings above and 4 `Project.milestones` findings
  of tests that query one project per request. Tests in
  `tests/django_project/test_statements.py`; this is not a corpus scenario,
  because an async view cannot share the corpus's transaction on SQLite.
- **One lookup helper called from two lines** (django-allauth): parents that
  one query line loaded are now also grouped by the call path of the read, so
  a helper that gets one object and reads its relation is a loop only when it
  runs in one (`clean_37`, `bug_38`). Label Studio lost 54
  `Task.project` findings the same way: a test that creates its annotations
  one by one at four lines, each save of which reads the task's project.

Fixed since: a finding whose rows a DRF view reads with no line of the
project in the call (a `ModelSerializer`, a generic view) pointed at the first
project frame on the stack. In paperless-ngx, Tandoor and Label Studio that
frame was a middleware's `get_response(request)` line. It now names the view
class, with the test line as its caller (see
[How it sees Django](#how-it-sees-django)). The same middleware, when it is
sync and the request async, was named for the async ORM calls of the view;
they now name the line that awaited them.

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

What each tool reports on the real projects (the final run of each set
above; reports counted once per test and message):

| Project | pytest-queryguard findings | django-zeal reports (tests) | nplusone reports (tests) |
|---|---|---|---|
| Django REST framework | 38 | 112 (97) | 36 (36), and 15 tests it broke |
| Read the Docs | 45 | 1445 (430) | 225 (138) |
| django-cms | 2539 | 2704 (980) | 768 (512) |
| pretix | 378 | 1479 (515) | 2756 (771) |
| saleor | 258 | 461 (292) | 1615 (1195) |
| django-oscar | 641 | 2875 (863) | 986 (495) |
| paperless-ngx | 167 | 509 (179) | 4876 (443) |
| Tandoor | 243 | 1013 (536) | 59 (31), and 403 tests it broke |
| django-allauth | 51 | 1168 (639) | 16 (16) |
| strawberry-django | 97 | 515 (307), and 1 test it broke | 257 (189), and 1 test it broke |
| Label Studio | 243 | 459 (101) | 482 (63) |

In Tandoor, nplusone broke 403 tests: Django's local-memory cache cannot
pickle a queryset once nplusone has patched `QuerySet._clone`.

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
