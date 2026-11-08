# Recipe: from a Sentry N+1 issue to a failing test

Sentry can open an "N+1 Query" performance issue for a request that runs one
query per row. Fixing the code closes it, but nothing stops the same loop from
coming back: Sentry only notices again after the next release, in
production. A test that fails on the loop today and passes after the fix keeps
it fixed in CI. This page goes from the issue to that test.

## What the issue tells you

Sentry's documentation
([N+1 Queries](https://docs.sentry.io/product/issues/issue-details/performance-issues/n-one-queries/),
read on 2026-11-08) lists what the issue shows under "Span Evidence":

- **Transaction name**: the request the queries ran in, which leads to the
  endpoint.
- **Parent span**: what groups the queries, such as a view or a serializer.
- **Preceding span**: the last span before the repeated queries, often the
  query that loaded the rows.
- **Repeating span**: the query that ran once per row, the "N".

Sentry raises the issue for sequential database spans with similar
descriptions, above a count (usually five) and a total duration (50 ms), with
at least one database span before them. Those thresholds are configurable in
Sentry; pytest-queryguard has its own (see below).

## From the issue to the code

1. **Find the endpoint** from the transaction name, and the function from the
   parent span.
2. **Find the loop** from the repeating span. Its description is the SQL: the
   table and the `WHERE` column say what is looked up per row. A lookup by
   primary key of another table is usually a lazy-loaded relationship (for
   example `SELECT customer.id, customer.name FROM customer WHERE customer.id = ?`
   under `Order.customer`); a `count(*)` or a lookup by foreign key is usually
   a query in a loop.
3. **Write a test that calls the endpoint with several rows.** One row is not
   enough: the N+1 rules need a relationship loaded for 2 parents, or one
   `SELECT` run 5 times. Ten rows is a safe size.
4. **Run it before fixing anything.** It should fail with a report that points
   at the same loop. If it passes, the test does not reach the code Sentry saw
   (other data, another branch); fix the test first.
5. **Fix the code**, run the test again, and commit the test with the fix.

## An example

A Flask-SQLAlchemy application whose `/customers` page shows each customer
with the number of their orders. Sentry reports (values made up):

- Transaction name: `GET /customers`
- Parent span: `customers`
- Preceding span: `SELECT customer.id, customer.name FROM customer`
- Repeating span: `SELECT count(*) AS count_1 FROM orders WHERE orders.customer_id = ?`

The repeating span is a count by foreign key: a query in a loop, not a
relationship. The project below was run with Flask 3.1 and Flask-SQLAlchemy
3.1; a test builds it from the files of this page and runs it.

The layout is `pytest.ini`, `app/__init__.py`, `tests/conftest.py` and
`tests/test_customers.py`.

`pytest.ini`:

```ini
[pytest]
pythonpath = .
```

`app/__init__.py`. `customers` is the code Sentry saw; `customers_fixed` is
the same page after the fix, kept next to it here so that both can be run. In
your code the fix replaces the function and the test stays:

```python
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import ForeignKey, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)


class Customer(db.Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]


class Order(db.Model):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"))


def create_app(database_uri: str = "sqlite:///app.db") -> Flask:
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_uri
    db.init_app(app)

    @app.get("/customers")
    def customers() -> list[dict[str, object]]:
        rows = []
        for customer in db.session.scalars(select(Customer)):
            orders = db.session.scalar(
                select(func.count()).select_from(Order).where(Order.customer_id == customer.id)
            )
            rows.append({"name": customer.name, "orders": orders})
        return rows

    @app.get("/customers-fixed")
    def customers_fixed() -> list[dict[str, object]]:
        stmt = (
            select(Customer.name, func.count(Order.id))
            .outerjoin(Order, Order.customer_id == Customer.id)
            .group_by(Customer.id)
            .order_by(Customer.id)
        )
        return [{"name": name, "orders": orders} for name, orders in db.session.execute(stmt)]

    return app
```

`tests/conftest.py` seeds ten customers with two orders each, in a fixture,
so the inserts are not measured:

```python
import pytest
from app import Customer, Order, create_app, db


@pytest.fixture
def client():
    app = create_app("sqlite://")
    with app.app_context():
        db.create_all()
        db.session.add_all(Customer(id=i, name=f"customer {i}") for i in range(1, 11))
        db.session.add_all(
            Order(customer_id=i) for i in range(1, 11) for _ in range(2)
        )
        db.session.commit()
    yield app.test_client()
    with app.app_context():
        db.drop_all()
```

`tests/test_customers.py`. The docstring names the issue, so that the next
reader knows where the test came from:

```python
import pytest


def test_customers_has_no_query_per_customer(client):
    """Sentry N+1 issue on GET /customers: one count query per customer."""
    response = client.get("/customers")
    assert response.status_code == 200
    assert len(response.json) == 10


@pytest.mark.max_queries(1)
def test_customers_fixed_has_no_query_per_customer(client):
    """The same test against the fixed page, with its query count pinned."""
    response = client.get("/customers-fixed")
    assert response.status_code == 200
    assert response.json[0] == {"name": "customer 1", "orders": 2}
```

Before the fix the first test fails on the same query as the issue, with the
line that runs it:

```
F.                                                                       [100%]
=================================== FAILURES ===================================
___________________ test_customers_has_no_query_per_customer ___________________
queryguard: N+1 detected

  the same statement ran 10 times
  first at app/__init__.py:35 in customers
    called from tests/test_customers.py:6 in test_customers_has_no_query_per_customer
  kind: repeated-statement  label: SELECT count(*) AS count_1 FROM orders WHERE orders.customer_id = ?
  docs: https://maciejziel.github.io/pytest-queryguard/how-detection-works.html#repeated-statement
  fix: fetch all rows in one query (WHERE id IN (...)) instead of in a loop
=========================== short test summary info ============================
FAILED tests/test_customers.py::test_customers_has_no_query_per_customer - Fa...
1 failed, 1 passed in 0.11s
```

After the fix the test passes. The `max_queries(1)` marker on the second test
also pins the count, so a change that adds one more query per customer in a
form the N+1 rules do not see still fails (see
[The query count must not grow with N](recipes-query-count-scaling.md)). The
marker needs no N+1 at all: `max_queries` fails on the number alone.

## When the test does not fail

- **Too few rows.** Five per-row queries are needed for the
  `repeated-statement` rule by default (`queryguard_repeat_threshold`), two
  parents for a relationship. Seed more rows, or check the call in a
  `record_queries()` block with a lower threshold:
  `log.assert_no_nplusone(repeat_threshold=2)` (see
  [Reading the queries](python-api.md)).
- **The objects are already in the session.** A many-to-one lazy load of an
  object the session already holds sends no SQL. Production loads the rows
  fresh; in the test, expire or expunge the objects after seeding, or seed in
  a fixture that commits and closes its session.
- **Another code path.** The parent span in Sentry names the code that ran;
  check that the test reaches it with the same parameters (filters, user,
  permissions).
- **The rules do not cover the pattern.** Pin the count with
  `@pytest.mark.max_queries(n)` or `queryguard.num_queries(n)` instead (see
  [Budgets and timing](budgets.md)); a budget fails on the number of
  statements whatever their shape.

pytest-queryguard can also send its own findings from a running application
to Sentry (see [In-application mode](in-app.md)); this page goes the other
way, from an issue Sentry raised to a test.
