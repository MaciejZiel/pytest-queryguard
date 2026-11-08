# Recipe: the query count must not grow with N

A list endpoint should send the same number of statements for 1 row as for
50. When the count grows with the number of rows, something runs once per row:
usually a lazy load, sometimes a lookup or a count in a loop. This page pins
that with a test: the same function with 1, 10 and 50 rows, and the same
count each time.

The N+1 rules already catch the usual cause: a relationship lazy-loaded for 2
or more parents, or one `SELECT` run 5 or more times, fails the test (see
[How detection works](how-detection-works.md)). The scaling test does a
different job. It writes the number down, so a change that adds a query per
row in a shape the rules do not recognise, a loop that stays under a
threshold, or one more constant query, fails too. And it says in the test what
the code promises: "one statement, whatever the size of the page".

Both tests below use only `record_queries()` and the `queryguard` fixture
(see [Reading the queries](python-api.md) and
[Budgets and timing](budgets.md)). The SQLAlchemy project was run with
SQLAlchemy 2.0 and 2.1; a test builds it from the files of this page and runs
it. The Django version is [at the end](#django).

## SQLAlchemy

The layout is `pytest.ini`, `app/__init__.py` and `tests/__init__.py` (both
empty), `app/models.py`, `app/orders.py`, `tests/conftest.py` and
`tests/test_orders.py`.

`pytest.ini`:

```ini
[pytest]
pythonpath = .
```

`app/models.py`:

```python
from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customer"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"))
    customer: Mapped[Customer] = relationship()
```

`app/orders.py`, the same list twice: `list_orders` loads the customers with
the orders, `list_orders_lazy` loads each customer when it is read:

```python
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Order


def list_orders(session: Session) -> list[dict[str, object]]:
    rows = []
    for order in session.scalars(select(Order).options(joinedload(Order.customer))):
        rows.append({"id": order.id, "customer": order.customer.name})
    return rows


def list_orders_lazy(session: Session) -> list[dict[str, object]]:
    rows = []
    for order in session.scalars(select(Order)):
        rows.append({"id": order.id, "customer": order.customer.name})
    return rows
```

`tests/conftest.py`: `make_session(rows)` builds a new database with that
many orders, and `session` uses it with the `rows` of the test. Seeding in a
fixture keeps the inserts out of what the plugin measures:

```python
from collections.abc import Callable, Iterator

import pytest
from app.models import Base, Customer, Order
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def make_session() -> Iterator[Callable[[int], Session]]:
    sessions = []

    def make(rows: int) -> Session:
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        session = Session(engine)
        ids = range(1, rows + 1)
        session.add_all(Customer(id=i, name=f"customer {i}") for i in ids)
        session.add_all(Order(id=i, customer_id=i) for i in ids)
        session.commit()
        session.expunge_all()  # the test loads the rows again, as a request would
        sessions.append(session)
        return session

    yield make
    for session in sessions:
        session.close()


@pytest.fixture
def rows() -> int:
    return 10


@pytest.fixture
def session(make_session: Callable[[int], Session], rows: int) -> Session:
    return make_session(rows)
```

### Pin the number

`@pytest.mark.parametrize("rows", ...)` overrides the `rows` fixture, so the
test runs three times, on 1, 10 and 50 orders. `queryguard.num_queries(1)`
fails unless the block sends exactly one statement:

`tests/test_orders.py`:

```python
import pytest
from app.orders import list_orders, list_orders_lazy
from pytest_queryguard import record_queries

SIZES = [1, 10, 50]


@pytest.mark.parametrize("rows", SIZES)
def test_list_orders_runs_one_query(queryguard, session, rows):
    with queryguard.num_queries(1):
        result = list_orders(session)
    assert len(result) == rows


@pytest.mark.parametrize("rows", SIZES)
def test_list_orders_lazy_runs_one_query(queryguard, session, rows):
    with queryguard.num_queries(1):
        result = list_orders_lazy(session)
    assert len(result) == rows


def test_list_orders_count_does_not_grow(make_session):
    counts = {}
    for rows in SIZES:
        session = make_session(rows)
        with record_queries() as log:
            list_orders(session)
        counts[rows] = log.count
    assert counts[1] == counts[10] == counts[50], counts
```

`list_orders` passes for every size. `list_orders_lazy` fails for each of
them, already with one row, because the block sends the order query and one
customer query per order:

```
$ pytest --tb=short
tests/test_orders.py ...FFF.                                             [100%]

=================================== FAILURES ===================================
___________________ test_list_orders_lazy_runs_one_query[1] ____________________
tests/test_orders.py:17: in test_list_orders_lazy_runs_one_query
    with queryguard.num_queries(1):
...
E   pytest_queryguard.QueryBudgetExceeded: queryguard: expected exactly 1 query, got 2
E
E     1. SELECT orders.id, orders.customer_id FROM orders  (app/orders.py:16 in list_orders_lazy)
E     2. SELECT customer.id, customer.name FROM customer WHERE customer.id = ?  (app/orders.py:17 in list_orders_lazy)
...
=========================== short test summary info ============================
FAILED tests/test_orders.py::test_list_orders_lazy_runs_one_query[1] - pytest...
FAILED tests/test_orders.py::test_list_orders_lazy_runs_one_query[10] - pytes...
FAILED tests/test_orders.py::test_list_orders_lazy_runs_one_query[50] - pytes...
3 failed, 4 passed in 0.30s
```

The cases with 10 and 50 rows fail with 11 and 51 statements, each listed
with its line. The budget failure is the one reported: when a test already
fails, the plugin does not add its N+1 report. Without the `num_queries` block
the N+1 rule would still fail those two cases on `Order.customer`, but not the
case with one row.

Use `num_queries(n)` when you know the number and want a change in either
direction to be a decision. `queryguard.max_queries(n)` accepts fewer.

### Compare the sizes without writing the number down

When the exact number is not the point (it depends on the database, or on
what else the endpoint does), `test_list_orders_count_does_not_grow` above
records each size with `record_queries()` and only asserts that the counts are
equal. A failure shows the counts by size, for example
`{1: 2, 10: 11, 50: 51}` for `list_orders_lazy`.

The sizes matter:

- With 1 row a per-row query cannot be told from a constant one, and the N+1
  rules do not fire. Always test with more than one row.
- 10 and 50 are above the default thresholds (2 parents for a relationship, 5
  runs of one `SELECT`), so the N+1 rules can also report what grows.
- Seed the rows outside the measured block. In a fixture (`session` above)
  nothing of the seeding is counted. A factory called in the test body
  (`make_session` in the last test) is left out of the `record_queries()`
  block, but the plugin still sees its statements as part of the test, so a
  default budget (`queryguard_max_queries` or a budget file) counts them.

## Django

The same with pytest-django, on the `Author` and `Book` models of
[Django: getting started](django.md#getting-started). Django support is
experimental. `book_lines` is application code; seeding uses `bulk_create`
so that the test body stays short:

```python
# library/reports.py
from library.models import Book


def book_lines():
    books = Book.objects.select_related("author")
    return [f"{book.title} by {book.author.name}" for book in books]
```

```python
# tests/test_book_scaling.py
import pytest

from library.models import Author, Book
from library.reports import book_lines
from pytest_queryguard import record_queries


def make_books(count):
    authors = Author.objects.bulk_create(
        Author(name=f"author {i}") for i in range(count)
    )
    Book.objects.bulk_create(
        Book(title=f"book {i}", author=author) for i, author in enumerate(authors)
    )


@pytest.mark.parametrize("count", [1, 10, 50])
def test_book_lines_runs_one_query(db, queryguard, count):
    make_books(count)
    with queryguard.num_queries(1):
        assert len(book_lines()) == count


def test_book_lines_count_does_not_grow(db):
    counts = {}
    for count in (1, 10, 50):
        Book.objects.all().delete()
        make_books(count)
        with record_queries() as log:
            book_lines()
        counts[count] = log.count
    assert counts[1] == counts[10] == counts[50], counts
```

`queryguard.num_queries()` is the plugin's equivalent of pytest-django's
`django_assert_num_queries`. Without `select_related("author")` both tests
fail: the first with 2, 11 and 51 statements, the second on the counts,
`{1: 2, 10: 11, 50: 51}`. Without the blocks, the N+1 rule alone would fail
the cases with 10 and 50 books on `Book.author`. A test in this repository
runs both versions with Django 5.2 to 6.1.
