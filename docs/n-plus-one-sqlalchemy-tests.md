# Detect N+1 queries in SQLAlchemy tests

Install pytest-queryguard and run your tests as usual. Every test is checked:
when the code it calls lazy-loads a relationship once per row, or sends the
same statement in a loop, the test fails and the report names the line and the
eager load that fixes it. There is no fixture, marker or setting to add.

```bash
pip install pytest-queryguard
```

It needs Python 3.10 or newer, pytest 8 or newer and SQLAlchemy 2.0.13 or
newer, and works with sync and async sessions, 2.0-style `select()`, the legacy
`Query` API and Core.

## Example

The models and the `session` fixture are those of
[Getting started](getting-started.md#a-first-test): 50 orders, each belonging
to a customer, in an in-memory SQLite database. The function lists the orders
with their customer's name.

`app/orders.py`:

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Order


def list_orders(session: Session) -> list[dict[str, object]]:
    rows = []
    for order in session.scalars(select(Order)).all():
        rows.append({"id": order.id, "customer": order.customer.name})
    return rows
```

`tests/test_order_list.py`:

```python
from app.orders import list_orders


def test_list_orders(session):
    assert len(list_orders(session)) == 50
```

The test passes without the plugin. With it, `pytest` fails it:

```
queryguard: N+1 detected

  Order.customer was lazy-loaded 50 times
  first at app/orders.py:10 in list_orders
    called from tests/test_order_list.py:5 in test_list_orders
  parents loaded at app/orders.py:9 in list_orders
  kind: relationship-load  label: Order.customer
  docs: https://maciejziel.github.io/pytest-queryguard/how-detection-works.html#relationship-load
  sql: SELECT customer.id, customer.name FROM customer WHERE customer.id = ?
  fix: load it with the parent query, e.g.
       select(Order).options(joinedload(Order.customer))
```

The loop sent 51 statements: one for the orders, then one per customer. The
fix loads the customers with the orders.

`app/orders_fixed.py`:

```python
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Order


def list_orders(session: Session) -> list[dict[str, object]]:
    rows = []
    for order in session.scalars(select(Order).options(joinedload(Order.customer))):
        rows.append({"id": order.id, "customer": order.customer.name})
    return rows
```

The same test against it passes, and with `max_queries(1)` it also checks
that the function sends a single statement.

`tests/test_order_list_fixed.py`:

```python
import pytest
from app.orders_fixed import list_orders


@pytest.mark.max_queries(1)
def test_list_orders(session):
    assert len(list_orders(session)) == 50
```

The test database needs at least two parent rows for an N+1 to show: by
default a relationship lazy-loaded by two parents from the same query is
reported (see [How detection works](how-detection-works.md#relationship-load)).
A repeated statement is reported from five runs.

## What else it reports

- the same `SELECT` sent in a loop, such as `session.get()` per id or a
  Core or `text()` query per row ([repeated statements](how-detection-works.md#repeated-statement));
- a test that sends more statements than a limit you set
  ([query budgets](budgets.md), or
  [Assert the number of SQL queries](count-queries-sqlalchemy-tests.md));
- when you turn them on, `INSERT`, `UPDATE` or `DELETE` sent once per row
  ([write loops](how-detection-works.md#write-loop)) and queries with no index
  to use on PostgreSQL, SQLite, MySQL and MariaDB ([index check](index-check.md)).

Loops written in the test itself or in its fixtures are not reported by
default: they never run in production
(see [Test code](how-detection-works.md#test-code)).

## Next

- [A test failed: now what](when-a-test-fails.md): check the finding, fix it
  or leave it out on purpose.
- Recipes for [FastAPI](recipes-fastapi.md),
  [Flask-SQLAlchemy](recipes-flask-sqlalchemy.md),
  [SQLModel](recipes-sqlmodel.md) and [Litestar](recipes-litestar.md).
- An existing suite with many findings: [Baseline](baseline.md) and
  [Adopting on a legacy codebase](recipes-legacy.md).
- Coming from nplusone, which does not see SQLAlchemy 2.0-style queries and
  does not import on 2.1:
  [Migrating from nplusone](migrating-from-nplusone.md).
