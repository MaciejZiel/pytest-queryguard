# Reading the queries in a test

`record_queries()` records the SQL statements a block sends and lets the
test read them back:

```python
from pytest_queryguard import record_queries

from app.customers import fetch_customers
from app.orders import list_orders


def test_list_orders_loads_customers_with_the_orders(session):
    with record_queries() as log:
        list_orders(session)
    assert log.count == 1
    assert "JOIN customer" in log.queries[0].sql
    assert all(query.relationship is None for query in log)


def test_fetch_customers(session):
    with record_queries() as log:
        fetch_customers(session, [1, 2, 3])
    log.assert_no_nplusone(repeat_threshold=2)
```

`log.queries` is a tuple of `Query` objects, in the order the statements
ran. The log is also sized, iterable and indexable (`len(log)`,
`for query in log`, `log[i]` and `log[a:b]`) but not a
`collections.abc.Sequence`, because `log.count` is a number and not the
`count(value)` method of a sequence. An empty log is falsy. Each `Query` has:

- `sql`: the statement as SQLAlchemy sent it to the driver;
- `callsite`: a `CallSite` with `filename`, `lineno` and `function`, the
  first frame outside SQLAlchemy, pytest, the standard library,
  `site-packages` and the plugin, or `None`;
- `relationship`: the relationship the statement loaded, lazily or as the
  extra query of `selectinload`, such as `"Order.customer"`, or `None`.

`log.count` is the number of statements. Both grow inside the block and
stay fixed after it. Statements from other threads are recorded too.

`QueryLog` is not constructible by users: `record_queries()` is the only way
to get one, and its constructor may change in any release. Use the class for
type hints only.

`log.assert_no_nplusone()` applies the plugin's two N+1 rules to the
block. It raises `NPlusOneDetected`, a subclass of `AssertionError`, with
the plugin's message, with paths relative to pytest's rootdir (outside
pytest, to the working directory). The thresholds are keyword arguments,
`relationship_threshold=2` and `repeat_threshold=5` by default, the same
defaults as the ini options.

`repeat_scope="session"` counts each `Session` on its own, like
`queryguard_repeat_scope = session` (see [Tests with several sessions or
requests](several-sessions.md)); the default is `"test"`.

It does not read the ini options, `queryguard_mode` or the `allow_nplusone`
marker; calling it is an explicit request. A project that sets
`queryguard_repeat_threshold = 10` in its configuration therefore has to pass
`repeat_threshold=10` here as well, or the helper keeps using 5 (the same for
`queryguard_relationship_threshold` and `relationship_threshold`). With the
`fetch_customers` loop from [the first test](getting-started.md#a-first-test), the
plugin accepts 3 lookups, because its threshold is 5, while the stricter
call above does not (`pytest --tb=short`, Python 3.13, SQLAlchemy 2.1):

```
tests/test_queries.py .F                                                 [100%]

=================================== FAILURES ===================================
_____________________________ test_fetch_customers _____________________________
tests/test_queries.py:18: in test_fetch_customers
    log.assert_no_nplusone(repeat_threshold=2)
E   pytest_queryguard.api.NPlusOneDetected: queryguard: N+1 detected
E   
E     the same statement ran 3 times
E     first at app/customers.py:9 in fetch_customers
E     kind: repeated-statement  label: SELECT customer.id, customer.name FROM customer WHERE customer.id = ?
E     fix: fetch all rows in one query (WHERE id IN (...)) instead of in a loop
=========================== short test summary info ============================
FAILED tests/test_queries.py::test_fetch_customers - pytest_queryguard.api.NP...
========================= 1 failed, 1 passed in 0.05s ==========================
```

If pytest-queryguard hits an internal error, it stops recording and issues a
`QueryGuardInternalWarning` that names the listener that failed; the
session summary repeats it with the error and the line that raised it. A `record_queries()` block that ends after such
an error then warns that its log may be incomplete (the warning class is
exported from `pytest_queryguard`), and `log.assert_no_nplusone()` raises
`RuntimeError` instead of passing on a log that may be empty. This also holds
outside pytest and with `--queryguard-mode=off`. The warning is issued when
the block ends normally; a block left by an exception does not warn, so the
warning never replaces the exception, but its log is still fixed at that
point and `assert_no_nplusone()` still refuses it.

What is public API, and what is not, is defined on the
[Stability and versioning](stability.md) page. In short: the names in
`pytest_queryguard.__all__` and the pytest options, markers and finding kinds
are public; the constructors of `QueryLog`, `Query` and `CallSite` are not.
`record_queries()` reads the queries; `max_queries()` and `max_db_time()` yield
the same `QueryLog`, so `with max_queries(3) as log:` also gives `log.count`,
`log.queries` and `log.slowest()`.

Recording changes nothing else. The plugin's N+1 detection, budgets and
index check see the same statements. Like `max_queries()`, `record_queries()`
also works outside pytest.

