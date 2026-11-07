# Django (experimental)

pytest-queryguard checks Django ORM code as it checks SQLAlchemy code: it
fails a test that runs an N+1, enforces query budgets, and writes the same
baseline, JSON and SARIF files. The Django support is **experimental** in this
release: its findings and options may still change in a minor release (see
[Stability](stability.md)).

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

Everything that works from these kinds works for Django: budgets
(`max_queries`, the `queryguard` fixture, the budget file), `record_queries()`,
`ignore()` blocks, the `queryguard_ignore` marker and option,
`--queryguard-index-check` with the index advisor, the
[baseline](baseline.md), `--queryguard-json` with the pull-request comparison
and the [GitHub Action](ci.md), [SARIF](sarif.md) and strict mode
(`queryguard_strict`, which raises `NPlusOneDetected` at the attribute access
that makes the N+1).

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
  Instances reached through `select_related()` have no line of their own;
  their loads are grouped by the line that read them.
- **Call sites** skip the frames of Django, asgiref and Django REST framework,
  as they skip SQLAlchemy's: a finding points at your view, serializer or test.
  A DRF view whose queryset is a class attribute has no line of its own: the
  finding points at the test line that called the view, and the fix belongs in
  the view's `queryset` or `get_queryset()`.

## Limitations

- **Values Django reloads by itself**: on a database without `RETURNING`
  (MySQL), a field saved as an expression (`F("pages") + 1`) is reloaded on
  its next read; such reads in a loop are reported as a `column-load` whose
  fix names `.only()`. Generated fields are left out.
- **Durations** include the time of other `execute_wrapper`s, since the
  plugin's wrapper is the outermost.
- **`queryguard_scope = session`** counts per database connection, which in a
  test is usually one: it does not split a test's requests as it does
  SQLAlchemy Sessions.
- **The async ORM** (`aget()`, `async for`) runs in a thread of asgiref:
  statements are measured, but their call site is the test line at best.
- Not for Django yet: `unused-eager-load`, `unused-columns`, `cartesian-load`,
  `--queryguard-write-patch` and `--queryguard-show-patch` (Django findings
  have no patch), the static check and the in-application mode.

## Accuracy

The Django corpus in `tests/django_project/corpus` has 68 scenarios, 34 with
an N+1 or another finding and 34 without, run in five configurations:
foreign keys, reverse relations, many-to-many relations, one-to-one relations,
generic relations,
multi-table inheritance, `select_related()`, `prefetch_related()` and
`Prefetch` objects, `.only()` and `.defer()`, Django REST framework
serializers, admin list, change and delete pages, templates, `get()` and
`exists()` per item, `save()` and `delete()` in a loop against bulk writes and
cascades, signals, `iterator()` and pagination. On SQLite, with Django 5.2 and
6.1, in the default configuration:

| Kind | Precision | Recall |
|---|---|---|
| `relationship-load` | 100 % (19 of 19) | 100 % |
| `column-load` | 100 % (5 of 5) | 100 % |
| `repeated-statement` | 100 % (7 of 7) | 100 % |
| `write-loop` (threshold 2) | 100 % (5 of 5) | 100 % |

The same holds in the other four configurations (small repeats, a column-load
threshold of 2, write loops, strict mode) and on PostgreSQL 17 and MySQL 8.4,
except for one scenario with two N+1s in strict mode: the test stops at the
first, before the second is complete. The corpus
was written together with the integration, so these numbers say that the
cases it names work, not how often the rules are right on other code; runs
on real Django projects are still to come. `scripts/django_accuracy_report.py`
prints them.

## Overhead

`scripts/bench_django_overhead.py` times three workloads in-memory on SQLite
with Django 6.1 and Python 3.13, each in a fresh process: one `get()` per
statement, iterating books with `select_related()` (the cost per instance)
and reading `book.author` of each book (a lazy load each). Median of 7 runs
on a laptop, in microseconds per operation:

| Workload | Not installed | Installed, not measuring | Measuring |
|---|---|---|---|
| `get()` | 139 | 144 (+4 %) | 163 (+17 %) |
| a row with `select_related()` | 7.5 | 7.3 (noise) | 7.7 (+3 %) |
| a lazy load | 159 | 167 (+5 %) | 207 (+30 %) |

The cost is per statement and per lazy load (finding the call site, recording
the event), and is smaller than the SQLAlchemy listeners' (see
[Performance](performance.md)). On a test suite, where statements are a part
of each test's time, the share is lower; a whole-suite measurement is still
to come.

