# Examples

Small projects you can run. Each one has a test that fails on purpose and a
test that passes after the fix, so you can see a finding and its cure.

| Directory | Shows |
|---|---|
| [sync-n-plus-one](sync-n-plus-one) | Sync SQLAlchemy: a lazy-load N+1, a loop of `session.get()` and a query budget |
| [async-fastapi](async-fastapi) | An async FastAPI endpoint with an N+1, tested through `TestClient` |
| [postgres-missing-index](postgres-missing-index) | The missing-index check on PostgreSQL, with a `compose.yaml` |
| [sqlite-index-check](sqlite-index-check) | The missing-index check on SQLite, no server needed |
| [baseline-sarif](baseline-sarif) | A baseline for existing findings, and a SARIF file |
| [action-sample](action-sample) | A project the GitHub Action is tested on in CI |

Run one from its own directory, with pytest-queryguard installed from this
repository (the baseline, SARIF and SQLite features are not in the 0.3.0
release on PyPI):

```console
$ pip install ../..            # or: pip install pytest-queryguard
$ pytest tests/test_orders.py        # fails: the finding
$ pytest tests/test_orders_fixed.py  # passes: the fix
```

Every example is run by `tests/test_examples.py` in CI, which checks that the
`test_orders.py` style files fail with the expected message and that the
`_fixed` files pass. The PostgreSQL example is skipped when `QUERYGUARD_PG_URL`
is not set.
