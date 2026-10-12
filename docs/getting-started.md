# Getting started

## Install

```bash
pip install pytest-queryguard
```

It needs Python 3.10 or newer, pytest 8 or newer and SQLAlchemy 2.0 or
newer. The plugin registers itself with pytest. There is nothing to import
or configure.


## A first test

Every test is checked automatically; you do not write anything to get N+1 detection. The example below is a small project. All output in this section is copied from a real run.


`app/orders.py`:

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Order


def list_orders(session: Session) -> list[dict[str, object]]:
    orders = session.scalars(select(Order)).all()
    return [{"id": order.id, "customer": order.customer.name} for order in orders]
```

`app/customers.py`:

```python
from sqlalchemy.orm import Session

from app.models import Customer


def fetch_customers(session: Session, ids: list[int]) -> list[Customer]:
    customers = []
    for customer_id in ids:
        customers.append(session.get(Customer, customer_id))
    return customers


def rename_customers(session: Session, names: dict[int, str]) -> None:
    for customer_id, name in names.items():
        session.get(Customer, customer_id).name = name
        session.flush()
```

`tests/test_orders.py` (the `session` fixture in `tests/conftest.py`
creates 50 customers with one order each; `tests/__init__.py` exists, so
pytest puts the project root on `sys.path` and `app` can be imported):

```python
import pytest

from app.customers import fetch_customers, rename_customers
from app.orders import list_orders


def test_list_orders(session):
    assert len(list_orders(session)) == 50


def test_fetch_customers(session):
    assert len(fetch_customers(session, list(range(1, 51)))) == 50


@pytest.mark.max_queries(2)
def test_rename_customers(session):
    rename_customers(session, {1: "Ada", 2: "Grace"})
```

All three tests pass without the plugin. With it installed,
`pytest --queryguard-report` gives the output below. It comes from Python
3.13 with SQLAlchemy 2.1; Python 3.12 prints the same. Older versions print
slightly different SQL or function names, for example `in <listcomp>` on
Python 3.10 and 3.11, or column aliases in the SQL on SQLAlchemy 2.0.

```
tests/test_orders.py FFF                                                 [100%]

=================================== FAILURES ===================================
_______________________________ test_list_orders _______________________________
queryguard: N+1 detected

  Order.customer was lazy-loaded 50 times
  first at app/orders.py:9 in list_orders
  sql: SELECT customer.id, customer.name FROM customer WHERE customer.id = ?
  fix: load it with the parent query, e.g.
       select(Order).options(joinedload(Order.customer))
_____________________________ test_fetch_customers _____________________________
queryguard: N+1 detected

  the same statement ran 50 times
  first at app/customers.py:9 in fetch_customers
  SELECT customer.id, customer.name FROM customer WHERE customer.id = ?
  fix: fetch all rows in one query (WHERE id IN (...)) instead of in a loop
____________________________ test_rename_customers _____________________________
queryguard: expected at most 2 queries, got 4

  1. SELECT customer.id, customer.name FROM customer WHERE customer.id = ?  (app/customers.py:15 in rename_customers)
  2. UPDATE customer SET name=? WHERE customer.id = ?  (app/customers.py:16 in rename_customers)
  3. SELECT customer.id, customer.name FROM customer WHERE customer.id = ?  (app/customers.py:15 in rename_customers)
  4. UPDATE customer SET name=? WHERE customer.id = ?  (app/customers.py:16 in rename_customers)
============================== queryguard report ===============================
   51  tests/test_orders.py::test_list_orders
   50  tests/test_orders.py::test_fetch_customers
    4  tests/test_orders.py::test_rename_customers
=========================== short test summary info ============================
FAILED tests/test_orders.py::test_list_orders - Failed: queryguard: N+1 detected
FAILED tests/test_orders.py::test_fetch_customers - Failed: queryguard: N+1 d...
FAILED tests/test_orders.py::test_rename_customers - Failed: queryguard: expe...
============================== 3 failed in 0.07s ===============================
```

The fixed code loads related rows with the parent query and fetches many
rows with one `IN` query.

`app/orders.py`, fixed:

```python
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Order


def list_orders(session: Session) -> list[dict[str, object]]:
    orders = session.scalars(select(Order).options(joinedload(Order.customer))).all()
    return [{"id": order.id, "customer": order.customer.name} for order in orders]
```

`app/customers.py`, fixed:

```python
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Customer


def fetch_customers(session: Session, ids: list[int]) -> Sequence[Customer]:
    return session.scalars(select(Customer).where(Customer.id.in_(ids))).all()


def rename_customers(session: Session, names: dict[int, str]) -> None:
    customers = session.scalars(select(Customer).where(Customer.id.in_(names)))
    for customer in customers:
        customer.name = names[customer.id]
    session.flush()
```

The same tests, unchanged:

```
tests/test_orders.py ...                                                 [100%]

============================== queryguard report ===============================
    2  tests/test_orders.py::test_list_orders
    2  tests/test_orders.py::test_rename_customers
    1  tests/test_orders.py::test_fetch_customers
============================== 3 passed in 0.04s ===============================
```


## Async and FastAPI

Nothing changes for async code. `AsyncSession` and `create_async_engine` run
on a sync engine underneath, which the plugin already watches. On SQLAlchemy
2.1 and newer, async support needs `pip install "sqlalchemy[asyncio]"`,
which provides greenlet. The failure
points to the `await` in your code, not to SQLAlchemy internals:

```python
# app/orders.py; the models use SQLAlchemy's AsyncAttrs mixin.
async def customer_names(session: AsyncSession) -> list[str]:
    orders = (await session.scalars(select(Order))).all()
    return [(await order.awaitable_attrs.customer).name for order in orders]
```

```python
# tests/test_async.py; main() creates the engine and data, then calls
# customer_names(session).
def test_customer_names():
    asyncio.run(main())
```

This fails with `Order.customer was lazy-loaded 3 times`, first at the line
with the `await`. pytest cannot run `async def` tests by itself. With
pytest-asyncio or anyio you can write the test as `async def` instead of
calling `asyncio.run`.

FastAPI's `TestClient` runs the application in another thread. Queries from
any thread are counted, so an N+1 inside an endpoint fails the test that
called it:

```python
def test_orders_endpoint(client):
    assert client.get("/orders").status_code == 200
```

