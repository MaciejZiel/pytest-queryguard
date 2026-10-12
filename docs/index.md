# pytest-queryguard

A pytest plugin that fails tests with N+1 queries and enforces query budgets
for SQLAlchemy 2, in sync, async and FastAPI code. On PostgreSQL 16 and newer
it can also fail tests whose queries have no index to use.

ORM code that looks correct can send one query per row instead of one query
per page. The tests pass, because a test database holds a handful of rows. In
production the same endpoint sends hundreds of queries. pytest-queryguard
watches the SQL each test sends, and when it sees the pattern it fails the
test and points to the line that caused it.

```
queryguard: N+1 detected

  Order.customer was lazy-loaded 50 times
  first at app/orders.py:9 in list_orders
  sql: SELECT customer.id, customer.name FROM customer WHERE customer.id = ?
  fix: load it with the parent query, e.g.
       select(Order).options(joinedload(Order.customer))
```

```bash
pip install pytest-queryguard
```

It is on by default: there is nothing to import or configure.

## Where to go

- New here: [Getting started](getting-started.md), then
  [How detection works](how-detection-works.md).
- Turning it up or down: [Configuration reference](configuration.md),
  [Budgets and timing](budgets.md), [Ignoring findings](ignoring.md).
- Adopting it on an existing project: [Baseline](baseline.md) and
  [CI and SARIF](ci.md).
- Missing indexes: [PostgreSQL](index-check.md) and the
  [index advisor](index-advisor.md).
- Questions: [FAQ](faq.md), [Limitations](limitations.md),
  [Stability and versioning](stability.md).

The source and the issue tracker are on
[GitHub](https://github.com/MaciejZiel/pytest-queryguard).
