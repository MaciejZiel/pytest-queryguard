# Design note: Django's own test runner (experimental)

Status: implemented as `pytest_queryguard.django`, experimental like the
rest of the Django support.

## Why

Most Django projects with N+1 issues that we looked at run their tests with
`manage.py test` (unittest `TestCase`s and Django's `DiscoverRunner`), not
pytest: 6 of 7 in the outreach research, some with a written rule not to
switch to pytest. The detection itself (the Django hooks in
`django_backend.py`, the collector, the detectors, the messages) does not need
pytest; only the per-test wiring in `plugin.py` does.

## Shape

- **`QueryGuardRunner`**, a `DiscoverRunner` subclass, set with
  `TEST_RUNNER = "pytest_queryguard.django.QueryGuardRunner"`. It measures
  every test. A project with a runner of its own subclasses it instead of
  `DiscoverRunner`.
- **`QueryGuardTestCase`**, a mixin for selective use under any runner:
  `class BookTests(QueryGuardTestCase, TestCase)`. Under pytest the plugin
  already measures each test, and the mixin does nothing.

No decorator: a test that must not be checked uses `ignore()` blocks or the
`ignore` patterns, as under pytest.

## The building block

Not the in-application mode (`pytest_queryguard.app`): its `Tracker` is a
per-request unit for SQLAlchemy's listeners, with sampling, budgets per route
and reporters, and its own repeat scopes. A test is what the plugin measures:
a `QueryCollector` pushed on the collector stack (`collector.push`), which
the Django hooks feed once `backends.install()` has run; then `detect()`,
`make_findings()` and `format_findings()`, as `plugin._findings` and
`plugin._analyse` do. The runner repeats those few steps (test-code split,
ignore patterns, `fail_on`, truncation note) rather than importing
`plugin.py`, which imports pytest and is tied to `pytest.Config`.

## Where a test starts and ends

unittest runs, inside `TestCase.run()`: `result.startTest()`, `setUp()`, the
test method, `tearDown()`, the cleanups (`doCleanups()`), the success or
failure report, then `result.stopTest()`. Django's `_pre_setup()` and
`_post_teardown()` (fixtures, transaction, flush) run outside `run()`, and
`django_backend` records nothing inside them anyway.

- The measurement starts in `startTest()` (the runner's result class) or at
  the start of `run()` (the mixin), and registers a cleanup first, so that it
  runs last: after `tearDown()` and the test's own cleanups.
- The cleanup pops the collector and analyses it. A failure raised there is
  reported by unittest as the test's failure (`addFailure`, before
  `addSuccess` would have been called), with the report as the message.
  `__unittest = True` in the module keeps its frames out of the traceback, as
  `pytest.fail(pytrace=False)` does in the plugin.
- `stopTest()` (or the end of the mixin's `run()`) ends a measurement whose
  cleanups never ran (a skipped test), drops the collector (the suite keeps
  every test case object until the end of the run), and shows the warnings.

This measures what pytest's call phase measures for a unittest test case:
`setUp()`, the test, `tearDown()`. `setUpClass()` and `setUpTestData()` are
not measured, like pytest fixtures. The same suite gives the same findings
under either runner.

A test that failed, errored, was skipped or failed as expected is not
analysed (`_outcome.success`), as under pytest, where the test's own failure
is reported alone.

## Warnings

In warn mode each report is a `QueryGuardWarning` at the test method's
`def` line. It is issued inside the cleanup under
`warnings.catch_warnings(record=True)`: an "error" filter
(`-W "error:queryguard:UserWarning"`) raises it there, which fails the test
as `filterwarnings = error` does under pytest; "ignore" drops it. The recorded warnings are shown from `stopTest()`,
after `--buffer` has restored stderr, so they are not swallowed with the
passing test's output.

## Settings

`settings.QUERYGUARD`, a dict with the ini options' names without the
`queryguard_` prefix: `mode`, `relationship_threshold`, `repeat_threshold`,
`small_repeat_threshold`, `column_load_threshold`, `write_loop_threshold`,
`scope`, `test_code`, `fail_on`, `ignore`, `max_events`, `stack_depth`, with
the plugin's defaults and checks. An unknown name is an error, so a typo or
an option that needs pytest (baseline, JSON, SARIF, budget files, strict
mode, index check) does not pass silently. `--queryguard-mode` on the command
line wins over the settings. `pytest.ini` and `pyproject.toml` are not read:
one project may run both runners with different settings.

Bad settings raise `ImproperlyConfigured` when the runner is created, before
any test database exists.

## Test code

The test-code classification (`callsite.TestCode`) gets the files of the
modules of the test cases in the suite, as the plugin gets the collected test
modules; the rest is unchanged (`tests/` and `test/` folders below the
working directory, which is the root, factories, `conftest.py`). Doctests'
modules are not added.

## `--parallel`

Django runs each subsuite in a worker with `RemoteTestRunner` and its
`RemoteTestResult`, and replays the events in the main process. The runner
sets `parallel_test_suite` to a subclass whose `runner_class` measures with a
`RemoteTestResult` subclass; the main process does not measure the replay.
Each worker reads the settings and installs the hooks on its first test.
With `spawn` or `forkserver` the command-line options go to the workers
through Django's `process_setup` hook. Failures travel back as ordinary
`AssertionError`s; warnings are shown by the worker on its stderr.

## Not in this first version

Budgets per test (the `max_queries()` context manager works, as
anywhere), the baseline, JSON, SARIF, the summary at the end of the run,
strict mode and the index check. Each needs a session-level part (merging
the workers' results, writing files) that the pytest plugin has and the
runner does not yet.
