# Assert the number of SQL queries in a SQLAlchemy test

SQLAlchemy has no `assertNumQueries` like Django's test case. With
pytest-queryguard installed, put `@pytest.mark.max_queries(n)` on a test to
fail it when its body sends more than `n` statements, or wrap a block in
`record_queries()` and assert on the count and the SQL yourself.

```bash
pip install pytest-queryguard
```

## Example

The models, the `session` fixture and the functions are those of
[Getting started](getting-started.md#a-first-test): 50 orders, each with a
customer. `list_orders` in `app/orders_fixed.py` loads the customers with
`joinedload`; `rename_customers` in `app/customers.py` loads and flushes one
customer at a time.

`tests/test_query_count.py`:

```python
import pytest
from app.customers import rename_customers
from app.orders_fixed import list_orders
from pytest_queryguard import record_queries


@pytest.mark.max_queries(1)
def test_list_orders_runs_one_query(session):
    assert len(list_orders(session)) == 50


def test_list_orders_reads_the_customers_in_the_same_query(session):
    with record_queries() as log:
        list_orders(session)
    assert log.count == 1
    assert "JOIN customer" in log.queries[0].sql


@pytest.mark.max_queries(2)
def test_rename_two_customers(session):
    rename_customers(session, {1: "Ada", 2: "Grace"})
```

The first two tests pass. The third fails and lists every statement with the
line that sent it:

```
queryguard: expected at most 2 queries, got 4

  1. SELECT customer.id, customer.name FROM customer WHERE customer.id = ?  (app/customers.py:15 in rename_customers)
  2. UPDATE customer SET name=? WHERE customer.id = ?  (app/customers.py:16 in rename_customers)
  3. SELECT customer.id, customer.name FROM customer WHERE customer.id = ?  (app/customers.py:15 in rename_customers)
  4. UPDATE customer SET name=? WHERE customer.id = ?  (app/customers.py:16 in rename_customers)
```

The marker counts the statements of the test body, writes included, and not
those of its fixtures. `record_queries()` counts the statements of its block,
from any thread, so it also works around a `TestClient` request; join worker
threads of your own before you read the count.

## Choosing the number

A fixed number breaks when the code legitimately changes. Two ways to avoid
picking one by hand:

- record today's count of every test in a budget file and fail only when a
  count grows: [Budgets and timing](budgets.md#lock-todays-counts-the-budget-file);
- run the code with 1, 10 and 50 rows and assert the count stays the same:
  [The query count must not grow with N](recipes-query-count-scaling.md).

Without any marker, every test is still checked for N+1 queries:
[Detect N+1 queries in SQLAlchemy tests](n-plus-one-sqlalchemy-tests.md).

## Next

- [Budgets and timing](budgets.md): the marker's options, a default budget
  for every test, and time limits.
- [Reading the queries](python-api.md): everything `record_queries()` returns,
  including the call site and duration of each statement.
- [Budgets for hot endpoints](recipes-budgets.md): where a budget pays off.
