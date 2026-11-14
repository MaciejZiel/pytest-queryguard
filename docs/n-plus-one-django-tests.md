# Find N+1 queries in Django tests with pytest

If your Django tests already run with pytest-django, install pytest-queryguard
next to it. Every test is then checked: when the code it calls reads a foreign
key, a reverse relation or a many-to-many relation once per object, the test
fails and the report names the line and the `select_related()` or
`prefetch_related()` that fixes it. Nothing goes into `INSTALLED_APPS`,
`MIDDLEWARE` or your settings.

```bash
pip install "pytest-queryguard[django]" pytest-django
```

It needs Django 5.2, 6.0 or 6.1. The Django support is **experimental** in
this release: its findings and options may still change in a minor release
(see [Stability](stability.md#experimental)).

## Example

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

The test passes without the plugin. With it, it fails at the end of the test
with:

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
writes are not counted: only the test function's body is.

## Compared with `django_assert_num_queries`

pytest-django's `django_assert_num_queries` and Django's `assertNumQueries`
check a number you choose, in the tests where you write them. pytest-queryguard
needs no number and checks every test. It also reports the same `SELECT` run
five or more times (`get()` or `exists()` per item), and its `max_queries`
marker and `record_queries()` give you counts when you want them
([Django: Collecting queries without failing the test](django.md#collecting-queries-without-failing-the-test)).

## Next

- [Django](django.md): every relation it checks, what it leaves out, its
  limits, and its accuracy against django-zeal and nplusone.
- [A test failed: now what](when-a-test-fails.md): check the finding, fix it
  or leave it out on purpose.
- [The query count must not grow with N](recipes-query-count-scaling.md): a
  scaling test with 1, 10 and 50 rows, for Django too.
- An existing suite with many findings: [Baseline](baseline.md).
