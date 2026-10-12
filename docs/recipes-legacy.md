# Recipe: adopting it on a legacy codebase

A suite that has never been checked will have findings. Turning the plugin
on in its default mode fails them all at once, which is not an adoption plan.
This one has three steps. The project is
[examples/sync-n-plus-one](https://github.com/MaciejZiel/pytest-queryguard/tree/main/examples/sync-n-plus-one)
plus one more module, added in step 3, so that there is a "new" finding at
the end. The output is from real runs (paths shortened, SQLAlchemy 2.1.4,
pytest 9.1; Python 3.14 also prints the test's first line under each
warning).

`test_rename_customers` has a `max_queries(2)` budget and runs 4 statements.
Budgets fail in every mode and are never baselined, so it would fail every run
below; the runs leave it out with
`--deselect tests/test_orders.py::test_rename_customers`. In your own suite,
fix such a test or raise its budget first.

## 1. Warn, and read

```toml
[tool.pytest.ini_options]
queryguard_mode = "warn"
```

Nothing fails because of a finding. Each one is a `QueryGuardWarning` in
pytest's warning summary:

```console
$ pytest -q --deselect tests/test_orders.py::test_rename_customers
```

```
..                                                                       [100%]
=============================== warnings summary ===============================
tests/test_orders.py::test_list_orders
  tests/test_orders.py:6: QueryGuardWarning: queryguard: N+1 detected
  
    Order.customer was lazy-loaded 50 times
    first at app/orders.py:10 in list_orders
    sql: SELECT customer.id, customer.name FROM customer WHERE customer.id = ?
    fix: load it with the parent query, e.g.
         select(Order).options(joinedload(Order.customer))

tests/test_orders.py::test_fetch_customers
  tests/test_orders.py:10: QueryGuardWarning: queryguard: N+1 detected
  
    the same statement ran 50 times
    first at app/customers.py:9 in fetch_customers
    SELECT customer.id, customer.name FROM customer WHERE customer.id = ?
    fix: fetch all rows in one query (WHERE id IN (...)) instead of in a loop

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
2 passed, 1 deselected, 2 warnings in 0.05s
```

Budgets (`max_queries`, `max_db_time`) still fail in `warn` mode: a budget is
a number you wrote, not a guess. Fix what is cheap, and let the rest wait for
the baseline. `queryguard_fail_on = ["relationship-load"]` is a middle step:
only that kind fails, the rest warn.

## 2. Baseline

Record what exists today, over the whole suite:

```console
$ pytest -q --deselect tests/test_orders.py::test_rename_customers \
    --queryguard-baseline=queryguard-baseline.json --queryguard-update-baseline
```

```
..                                                                       [100%]
========================= queryguard: baseline updated =========================
queryguard-baseline.json: 2 entries (2 added, 0 removed)
2 passed, 1 deselected in 0.05s
```

The file lists each finding by kind, label, file and function, not by line, so
moving code does not invalidate it:

```json
{
  "version": 1,
  "findings": [
    {
      "kind": "relationship-load",
      "label": "Order.customer",
      "file": "app/orders.py",
      "function": "list_orders"
    },
    {
      "kind": "repeated-statement",
      "label": "SELECT customer.id, customer.name FROM customer WHERE customer.id = ?",
      "file": "app/customers.py",
      "function": "fetch_customers"
    }
  ]
}
```

Commit it and turn it on, and go back to the default mode:

```toml
[tool.pytest.ini_options]
queryguard_baseline = "queryguard-baseline.json"
```

## 3. Fail on anything new

With `queryguard_mode` unset (so `fail`) and the baseline in place, the old
findings pass. A new module with a loop of queries (`app/reports.py`) fails
its test (`tests/test_new.py` calls it with ten names):

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Customer


def customer_ids_by_name(session: Session, names: list[str]) -> list[int]:
    ids = []
    for name in names:
        ids.append(
            session.scalars(select(Customer.id).where(Customer.name == name)).one()
        )
    return ids
```

```
F..                                                                      [100%]
=================================== FAILURES ===================================
__________________________ test_customer_ids_by_name ___________________________
queryguard: N+1 detected

  the same statement ran 10 times
  first at app/reports.py:11 in customer_ids_by_name
  SELECT customer.id FROM customer WHERE customer.name = ?
  fix: fetch all rows in one query (WHERE id IN (...)) instead of in a loop
=========================== short test summary info ============================
FAILED tests/test_new.py::test_customer_ids_by_name - Failed: queryguard: N+1...
1 failed, 2 passed, 1 deselected in 0.09s
```

As the old code gets fixed, entries stop matching. A run that matches none
of them lists the stale ones, so the file can shrink:

```
...                                                                      [100%]
====================== queryguard: stale baseline entries ======================
2 baseline entries in queryguard-baseline.json matched no finding in this run:
  relationship-load Order.customer (app/orders.py, list_orders)
  repeated-statement SELECT customer.id, customer.name FROM customer WHERE customer.id = ? (app/customers.py, fetch_customers)
They are fixed, or this run did not cover them. After a full run, prune them with --queryguard-update-baseline.
3 passed in 0.04s
```

After a full run, `--queryguard-update-baseline` prunes them. Review the diff of
the baseline file in pull requests: a growing file means findings are being
accepted, not fixed. See [Baseline](baseline.md) for the details, and
[CI](recipes-ci.md) for running this on every pull request.
