# Budgets and timing

Budgets fail a test that runs too many statements or spends too long in the database. They do not depend on N+1 detection and always fail, whatever `queryguard_mode` is set to.

## Query budgets with the `max_queries` marker

```python
import pytest


@pytest.mark.max_queries(3)
def test_order_page(client):
    client.get("/orders/1")


@pytest.mark.max_queries(limit=3)  # the same, with a keyword
def test_order_page_again(client):
    client.get("/orders/1")
```

The keyword is `limit`, as in the function below. Releases before 0.4 called
it `n`; `n=3` keeps working, without a warning. It may be removed in a future
minor release, but only after a deprecation period with a warning (see
[Stability](stability.md)).

The test fails when its body runs more SQL statements than the limit. The
failure lists every statement with the line that issued it. Budgets count
all statements, writes included, and they always fail, whatever
`queryguard_mode` is set to. If the test already fails on its own, an
exceeded budget is not reported, because the original failure wins.

## Query budgets with the `max_queries()` context manager

```python
from pytest_queryguard import max_queries


def test_checkout(service, cart):
    with max_queries(2):
        service.checkout(cart)
```

Only the statements inside the `with` block count. Above the limit it raises
`QueryBudgetExceeded`, a subclass of `AssertionError`. The context manager
does not depend on pytest, so it also works in a script or a benchmark. It
checks the budget only; N+1 detection is done by the plugin around the
whole test.

## Time budgets with `max_db_time`

```python
import pytest
from pytest_queryguard import max_db_time


@pytest.mark.max_db_time(0.25)  # seconds
def test_order_page(client):
    client.get("/orders/1")


def test_checkout(service, cart):
    with max_db_time(0.1):
        service.checkout(cart)
```

The limit is the time the database and the driver took for the statements,
measured around each cursor call, so slow Python code in the test does not
count. The failure shows the total and the slowest statements with the line
that issued them. A `max_queries` failure shows the total database time and
its three slowest statements too. Like `max_queries`, the budget fails
whatever `queryguard_mode` is set to. In `record_queries()`, each `Query` has
a `duration` (seconds), and the log has `db_time` and `slowest(n)`.

