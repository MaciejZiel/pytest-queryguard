"""N+1 detection under Django's own test runner (``manage.py test``), experimental.

``QueryGuardRunner`` is a ``DiscoverRunner`` (``TEST_RUNNER``) that measures
every test as the pytest plugin measures a test's call phase, and fails or
warns per finding; ``QueryGuardTestCase`` is a mixin that does the same for
the test cases it is mixed into, under any runner. The settings come from
``settings.QUERYGUARD``; see docs/django.md and docs/design-django-runner.md.

What is measured is what pytest runs in a unittest test's call phase:
``setUp()``, the test method, ``tearDown()`` and the cleanups. Not
``setUpClass()`` or ``setUpTestData()``, and not the database set-up of
Django's test cases (``django_backend`` records nothing inside
``_pre_setup()`` and ``_post_teardown()``).

The measurement starts in ``TestResult.startTest()`` (or in the mixin's
``run()``) and is analysed in a cleanup registered first, so it runs last,
after ``tearDown()`` and the test's own cleanups, while unittest still
reports a failure raised there as the test's failure. ``stopTest()`` ends a
measurement the cleanups never reached (a skipped test) and shows the
warnings of warn mode, after ``--buffer`` has given the output back.
"""

from __future__ import annotations

import contextlib
import inspect
import sys
import unittest
import warnings
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, fields, replace
from fnmatch import fnmatchcase
from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ImproperlyConfigured
from django.test.runner import (
    DiscoverRunner,
    ParallelTestSuite,
    RemoteTestResult,
    RemoteTestRunner,
)

from . import blocks
from .backends import install
from .callsite import DEFAULT_STACK_DEPTH, TestCode, swap_stack_depth, swap_test_code
from .collector import QueryCollector, pop, push
from .detectors import (
    DEFAULT_COLUMN_LOAD_THRESHOLD,
    SCOPE_TEST,
    SCOPES,
    detect,
)
from .errors import internal_error, internal_failure, record_error, swap_error
from .findings import Finding
from .ignores import IgnoreRule, apply_rules, check_kind, parse_rules
from .messages import format_findings, format_truncation, make_findings, swap_root
from .warnings import QueryGuardInternalWarning, QueryGuardWarning, warn_internal

__all__ = ["QueryGuardRunner", "QueryGuardTestCase"]

# unittest leaves the frames of a module with this global out of a failure's
# traceback: a finding is reported as its message alone, as the plugin's
# pytest.fail(pytrace=False) does.
__unittest = True

MODES = ("fail", "warn", "off")
_TEST_CODE_MODES = ("off", "warn", "fail")
DEFAULT_MAX_EVENTS = 10_000  # the plugin's default (plugin.DEFAULT_MAX_EVENTS)
# the attribute of a test case that holds its running measurement
_ATTR = "_queryguard_measurement"


# --- settings ---------------------------------------------------------------


@dataclass(frozen=True)
class Config:
    """``settings.QUERYGUARD``, read and checked; names as the ini options'
    without the ``queryguard_`` prefix."""

    mode: str = "fail"
    relationship_threshold: int = 2
    repeat_threshold: int = 5
    small_repeat_threshold: int | None = None
    column_load_threshold: int = DEFAULT_COLUMN_LOAD_THRESHOLD
    write_loop_threshold: int | None = None
    scope: str = SCOPE_TEST
    test_code: str = "off"
    fail_on: tuple[str, ...] = ()
    ignore: tuple[IgnoreRule, ...] = ()
    max_events: int | None = DEFAULT_MAX_EVENTS
    stack_depth: int = DEFAULT_STACK_DEPTH

    def fails(self, finding: Finding) -> bool:
        """True when the finding fails the test; else it is only a warning."""
        if self.mode != "fail":
            return False
        if finding.test_code and self.test_code != "fail":
            return False
        return not self.fail_on or any(
            fnmatchcase(finding.kind, p) for p in self.fail_on
        )


_KNOWN = frozenset(f.name for f in fields(Config))


def _bad(name: str, message: str) -> ImproperlyConfigured:
    return ImproperlyConfigured(f"QUERYGUARD[{name!r}] {message}")


def _integer(name: str, raw: object, minimum: int, *, zero: bool = False) -> int:
    value = -1
    if not isinstance(raw, bool) and isinstance(raw, int | str):
        with contextlib.suppress(ValueError):
            value = int(raw)
    if (zero and value == 0) or value >= minimum:
        return value
    allowed = f"0 or an integer >= {minimum}" if zero else f"an integer >= {minimum}"
    raise _bad(name, f"must be {allowed}, got {raw!r}")


def _choice(name: str, raw: object, choices: tuple[str, ...]) -> str:
    value = str(raw).strip().lower()
    if value not in choices:
        raise _bad(name, f"must be one of {', '.join(choices)}, got {raw!r}")
    return value


def _lines(name: str, raw: object) -> list[str]:
    """A list of strings, or one string of lines, as an ini linelist."""
    if isinstance(raw, str):
        raw = raw.splitlines()
    if not isinstance(raw, list | tuple) or not all(isinstance(x, str) for x in raw):
        raise _bad(name, f"must be a list of strings, got {raw!r}")
    return [
        line.strip()
        for line in raw
        if line.strip() and not line.strip().startswith("#")
    ]


def read_config(
    raw: Mapping[str, Any] | None, overrides: Mapping[str, Any] | None = None
) -> Config:
    """The settings: ``overrides`` (the command line) over ``raw``, over the defaults.

    Raises ``ImproperlyConfigured`` for an unknown name or a bad value.
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ImproperlyConfigured(f"QUERYGUARD must be a dict, got {raw!r}")
    unknown = sorted(set(raw) - _KNOWN)
    if unknown:
        raise ImproperlyConfigured(
            f"QUERYGUARD: unknown option(s) {', '.join(map(repr, unknown))}; "
            f"known: {', '.join(sorted(_KNOWN))} (the baseline, JSON, SARIF, "
            "budget files, strict mode and the index check need pytest)"
        )
    values = {**raw, **{k: v for k, v in (overrides or {}).items() if v is not None}}
    kwargs: dict[str, Any] = {}
    if "mode" in values:
        kwargs["mode"] = _choice("mode", values["mode"], MODES)
    for name in ("relationship_threshold", "repeat_threshold", "column_load_threshold"):
        if name in values:
            kwargs[name] = _integer(name, values[name], 2)
    for name in ("small_repeat_threshold", "write_loop_threshold"):
        if name in values:
            kwargs[name] = _integer(name, values[name], 2, zero=True) or None
    repeat = kwargs.get("repeat_threshold", Config.repeat_threshold)
    small = kwargs.get("small_repeat_threshold")
    if small is not None and small >= repeat:
        raise _bad(
            "small_repeat_threshold",
            f"must be lower than repeat_threshold ({repeat}), got {small}",
        )
    if "scope" in values:
        kwargs["scope"] = _choice("scope", values["scope"], SCOPES)
    if "test_code" in values:
        kwargs["test_code"] = _choice(
            "test_code", values["test_code"], _TEST_CODE_MODES
        )
    if "fail_on" in values:
        kinds = _lines("fail_on", values["fail_on"])
        for kind in kinds:
            try:
                check_kind(kind)
            except ValueError as exc:
                raise _bad("fail_on", str(exc)) from None
        kwargs["fail_on"] = tuple(kinds)
    if "ignore" in values:
        try:
            kwargs["ignore"] = parse_rules(_lines("ignore", values["ignore"]))
        except ValueError as exc:
            raise _bad("ignore", str(exc)) from None
    if "max_events" in values:
        kwargs["max_events"] = _integer("max_events", values["max_events"], 0) or None
    if "stack_depth" in values:
        kwargs["stack_depth"] = _integer("stack_depth", values["stack_depth"], 0)
    return Config(**kwargs)


# Options the runner's command line sets, over settings.QUERYGUARD; sent to
# the workers of --parallel that do not fork (QueryGuardParallelTestSuite).
_overrides: dict[str, Any] = {}


# --- per-process state -------------------------------------------------------


@dataclass
class _State:
    config: Config
    root: Path
    test_code: TestCode
    modules: set[str] = field(default_factory=set)
    internal_reported: bool = False

    def note_tests(self, tests: Iterable[Any]) -> None:
        """The files of these tests' modules are test code."""
        files: set[str] = set()
        for test in tests:
            path = _module_file(test)
            if path is not None and path not in self.modules:
                files.add(path)
        if files:
            self.modules.update(files)
            # clears the classification cache: only when a file is new
            self.test_code.add_files(files)


_state: _State | None = None


def _get_state() -> _State:
    """The state of this process, made on first use (each --parallel worker
    makes its own); raises ``ImproperlyConfigured`` for bad settings."""
    global _state
    if _state is None:
        from django.conf import settings

        config = read_config(getattr(settings, "QUERYGUARD", None), _overrides)
        root = Path.cwd()
        test_code = TestCode(str(root))
        if config.mode != "off" and not _plugin_active():
            # (under the pytest plugin, which measures, its own values stay)
            install()
            swap_root(root)
            swap_test_code(test_code)
            swap_stack_depth(config.stack_depth)
        _state = _State(config, root, test_code)
    return _state


def _module_file(test: Any) -> str | None:
    """The file of a test case's module; None for a doctest or no file."""
    name = type(test).__module__
    if (
        not isinstance(test, unittest.TestCase)
        or name == "doctest"
        or name.split(".")[0] == "unittest"  # _FailedTest, FunctionTestCase
    ):
        return None
    module = sys.modules.get(type(test).__module__)
    path = getattr(module, "__file__", None)
    return path if isinstance(path, str) else None


def _iter_tests(suite: Any) -> Iterable[Any]:
    """The test cases of a (nested) suite."""
    if isinstance(suite, unittest.TestCase):
        yield suite
        return
    try:
        children = list(suite)
    except TypeError:
        return
    for child in children:
        yield from _iter_tests(child)


def _plugin_active() -> bool:
    """Whether the pytest plugin measures this run: it measures each test then."""
    plugin = sys.modules.get("pytest_queryguard.plugin")
    return bool(getattr(plugin, "_active", None))


# --- one test ----------------------------------------------------------------


@dataclass
class _Measurement:
    collector: QueryCollector | None
    done: bool = False
    shown: list[warnings.WarningMessage] = field(default_factory=list)


def _begin(test: Any) -> None:
    """Start measuring ``test``; a no-op when measuring is off or already on."""
    if not isinstance(test, unittest.TestCase) or test.__dict__.get(_ATTR) is not None:
        return
    if _plugin_active():
        return
    state = _get_state()  # ImproperlyConfigured goes on (the runner checks first)
    if state.config.mode == "off" or internal_error() is not None:
        return
    try:
        state.note_tests((test,))
        collector = QueryCollector(max_events=state.config.max_events)
        push(collector)
    except Exception as exc:  # noqa: BLE001  # a bug here must never break the suite
        record_error(exc, "start of the measurement")
        return  # reported after the test (_end)
    measurement = _Measurement(collector)
    setattr(test, _ATTR, measurement)
    # first in, last out: after tearDown() and the test's own cleanups
    test.addCleanup(_finish, test, measurement)


def _finish(test: unittest.TestCase, measurement: _Measurement) -> None:
    """The cleanup: stop measuring, analyse, fail or note the warnings."""
    if measurement.done:
        return
    collector = _stop(measurement)
    if collector is None:
        return
    state = _get_state()
    outcome = getattr(test, "_outcome", None)
    if outcome is not None and (
        not outcome.success or getattr(outcome, "expectedFailure", None)
    ):
        # A test that failed (or was skipped) is reported for its own
        # failure, as under pytest, where its findings are not analysed.
        return
    failing, warning = _analyse(test, collector, state)
    if failing:
        # as the plugin: the findings that only warn go under the failure
        error = test.failureException("\n\n".join([*failing, *warning]))
        error._queryguard = True  # type: ignore[attr-defined]
        raise error
    if warning:
        measurement.shown = _warn(test, warning)


def _stop(measurement: _Measurement) -> QueryCollector | None:
    measurement.done = True
    collector, measurement.collector = measurement.collector, None
    if collector is None:
        return None
    try:
        pop(collector)
    except Exception as exc:  # noqa: BLE001  # a bug here must never break the suite
        record_error(exc, "end of the measurement")
        return None
    return collector


def _end(test: Any) -> None:
    """After the test: end a measurement its cleanups never ran (a skip) and
    show the warnings. Drops the events: the suite keeps every test case."""
    if _state is not None:
        # here, not in the cleanup: --buffer has given stderr back, and a
        # test that failed or a measurement that never started reports too
        _report_internal_error(_state)
    measurement = getattr(test, "__dict__", {}).pop(_ATTR, None)
    if measurement is None:
        return
    if not measurement.done:
        _stop(measurement)
        # a skip returns before the cleanups run: drop ours, which holds the test
        cleanups = getattr(test, "_cleanups", None)
        if isinstance(cleanups, list):
            cleanups[:] = [
                cleanup
                for cleanup in cleanups
                if not (cleanup[0] is _finish and cleanup[1][1:] == (measurement,))
            ]
    for shown in measurement.shown:
        with contextlib.suppress(Exception):
            warnings.showwarning(
                shown.message, shown.category, shown.filename, shown.lineno
            )
    measurement.shown = []


def _analyse(
    test: unittest.TestCase, collector: QueryCollector, state: _State
) -> tuple[list[str], list[str]]:
    """(texts that fail the test, texts that only warn); never raises."""
    if internal_error() is not None:
        return [], []
    config = state.config
    try:
        root = state.root
        sieve = blocks.Sieve()
        split = config.test_code != "fail"
        findings = make_findings(
            _detect(collector, config, sieve, False if split else None), cwd=root
        )
        if config.test_code == "warn" and _has_test_code(collector):
            findings.extend(
                replace(finding, test_code=True)
                for finding in make_findings(
                    _detect(collector, config, sieve, True), cwd=root
                )
            )
        if config.ignore:
            findings = apply_rules(findings, config.ignore)[0]
        note = ""
        if collector.truncated and config.max_events is not None:
            note = "\n\n" + format_truncation(config.max_events)
        failing = [f for f in findings if config.fails(f)]
        warning = [f for f in findings if not config.fails(f)]
        return (
            [format_findings(failing, cwd=root) + note] if failing else [],
            [format_findings(warning, cwd=root) + note] if warning else [],
        )
    except Exception as exc:  # noqa: BLE001  # a bug here must never break the suite
        record_error(exc, "N+1 analysis")
        return [], []


def _detect(
    collector: QueryCollector,
    config: Config,
    sieve: blocks.Sieve,
    test_code: bool | None,
) -> list[Any]:
    return detect(
        collector,
        relationship_threshold=config.relationship_threshold,
        repeat_threshold=config.repeat_threshold,
        column_load_threshold=config.column_load_threshold,
        small_repeat_threshold=config.small_repeat_threshold,
        write_loop_threshold=config.write_loop_threshold,
        scope=config.scope,
        sieve=sieve,
        test_code=test_code,
    )


def _has_test_code(collector: QueryCollector) -> bool:
    """Whether any statement or load of the test ran in test code."""
    return any(
        event.test_code
        for events in (
            collector.statements,
            collector.relationship_loads,
            collector.column_loads,
        )
        for event in events
    )


def _location(test: unittest.TestCase) -> tuple[str, int]:
    """The file and line of the test method, for its warnings."""
    try:
        method = inspect.unwrap(getattr(test, test._testMethodName))
        code = method.__code__
        return code.co_filename, code.co_firstlineno
    except Exception:  # noqa: BLE001  # an odd test method: its module instead
        return _module_file(test) or "<unknown>", 1


def _warn(test: unittest.TestCase, texts: list[str]) -> list[warnings.WarningMessage]:
    """Issue each text as a ``QueryGuardWarning`` at the test; the ones the
    filters let through, to show after the test (``_end``).

    An "error" filter raises it here, in the cleanup, which fails the test as
    ``filterwarnings = error`` does under pytest; "ignore" drops it.
    """
    filename, lineno = _location(test)
    with warnings.catch_warnings(record=True) as caught:
        for text in texts:
            warning = QueryGuardWarning(text)
            # raised by an "error" filter: sent from a --parallel worker
            # without its traceback (QueryGuardRemoteTestResult)
            warning._queryguard = True  # type: ignore[attr-defined]
            warnings.warn_explicit(warning, QueryGuardWarning, filename, lineno)
    return caught


def _report_internal_error(state: _State) -> None:
    error = internal_error()
    if error is None or state.internal_reported:
        return
    state.internal_reported = True
    failure = internal_failure()
    where = f" ({failure.source})" if failure is not None else ""
    warn_internal(
        QueryGuardInternalWarning(
            f"pytest-queryguard disabled itself after an internal error: {error!r}"
            f"{where}"
        )
    )


# --- the test case mixin -----------------------------------------------------

if TYPE_CHECKING:
    _TestCaseBase = unittest.TestCase
else:
    _TestCaseBase = object


class QueryGuardTestCase(_TestCaseBase):
    """Measure each test of the test cases this is mixed into, under any runner.

    ``class BookTests(QueryGuardTestCase, TestCase)``. Under
    ``QueryGuardRunner`` every test is measured already; under pytest the
    plugin measures it and the mixin does nothing.
    """

    def run(self, result: unittest.TestResult | None = None) -> Any:
        _begin(self)
        try:
            return super().run(result)
        finally:
            _end(self)


# --- the runner --------------------------------------------------------------


class _GuardedResult:
    """A result class that measures every test it runs."""

    def startTest(self, test: Any) -> None:  # unittest's name
        super().startTest(test)  # type: ignore[misc]
        _begin(test)

    def stopTest(self, test: Any) -> None:  # unittest's name
        try:
            super().stopTest(test)  # type: ignore[misc]
        finally:
            # after the base class: --buffer has given stderr back
            _end(test)


class QueryGuardRemoteTestResult(_GuardedResult, RemoteTestResult):  # type: ignore[misc]
    """The result of a --parallel worker, measuring each test."""

    def addFailure(self, test: Any, err: Any) -> None:
        super().addFailure(test, _own(err))

    def addError(self, test: Any, err: Any) -> None:
        super().addError(test, _own(err))


def _own(err: Any) -> Any:
    """A failure of this module without its traceback: the report is the
    whole message, the traceback only this module's frames, and without
    tblib it could not be pickled back to the main process (the run stops)."""
    if err is not None and getattr(err[1], "_queryguard", False):
        return (err[0], err[1], None)
    return err


class QueryGuardRemoteTestRunner(RemoteTestRunner):  # type: ignore[misc]
    resultclass = QueryGuardRemoteTestResult

    def run(self, test: Any) -> Any:
        try:
            state = _get_state()
            if state.config.mode != "off":
                state.note_tests(_iter_tests(test))
        except ImproperlyConfigured:
            raise
        except Exception as exc:  # noqa: BLE001  # a bug here must never break the suite
            record_error(exc, "test modules")
        return super().run(test)


def _process_setup(overrides: Mapping[str, Any]) -> None:
    """In a worker that does not fork: the options of the command line."""
    _overrides.update(overrides)


class QueryGuardParallelTestSuite(ParallelTestSuite):  # type: ignore[misc]
    runner_class = QueryGuardRemoteTestRunner
    process_setup = _process_setup


_result_classes: dict[type, type] = {}


def _guarded(base: type) -> type:
    """``base`` with the measurement mixed in; one class per base."""
    guarded = _result_classes.get(base)
    if guarded is None:
        guarded = _result_classes[base] = type(
            f"QueryGuard{base.__name__}", (_GuardedResult, base), {}
        )
    return guarded


def _wrap_run(test: unittest.TestCase) -> None:
    """Measure ``test`` whatever result class runs it, as the mixin does."""
    original = test.run

    def run(result: unittest.TestResult | None = None) -> Any:
        _begin(test)
        try:
            return original(result)
        finally:
            _end(test)

    test.run = run  # type: ignore[method-assign]


class QueryGuardRunner(DiscoverRunner):  # type: ignore[misc]
    """``DiscoverRunner`` that fails (or warns about) the N+1s of each test.

    ``TEST_RUNNER = "pytest_queryguard.django.QueryGuardRunner"``, configured
    by ``QUERYGUARD = {...}`` in the settings and ``--queryguard-mode``.
    """

    parallel_test_suite = QueryGuardParallelTestSuite

    def __init__(self, *args: Any, queryguard_mode: str | None = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        global _state
        # a runner of its own (call_command("test") twice in one process):
        # the settings and the command line are read again
        _state = None
        _overrides.clear()
        if not _plugin_active():
            swap_error(None)  # an error of an earlier run in this process
        if queryguard_mode is not None:
            _overrides["mode"] = queryguard_mode
        self._queryguard_parallel = False
        self._queryguard_wrapped = False
        # bad settings stop the run here, before a database is created
        _get_state()

    @classmethod
    def add_arguments(cls, parser: Any) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--queryguard-mode",
            choices=MODES,
            default=None,
            help="pytest-queryguard: fail, warn or off; overrides QUERYGUARD['mode'].",
        )

    def build_suite(self, *args: Any, **kwargs: Any) -> Any:
        suite = super().build_suite(*args, **kwargs)
        state = _get_state()
        if isinstance(suite, ParallelTestSuite):
            if state.config.mode != "off" and not isinstance(
                suite, QueryGuardParallelTestSuite
            ):
                # its workers would run Django's own result: nothing measured
                raise ImproperlyConfigured(
                    "pytest-queryguard: with --parallel, the parallel_test_suite "
                    "of a QueryGuardRunner subclass must subclass "
                    "pytest_queryguard.django.QueryGuardParallelTestSuite; "
                    f"{type(suite).__qualname__} does not"
                )
            # the workers measure; the main process only replays their events
            self._queryguard_parallel = True
            suite.process_setup_args = (dict(_overrides),)
        elif state.config.mode != "off" and self._own_resultclass():
            # a test runner that picks its own result class when given none
            # (xmlrunner's XMLTestRunner): measure the tests, not the result
            self._queryguard_wrapped = True
            for test in _iter_tests(suite):
                _wrap_run(test)
        if state.config.mode != "off":
            try:
                state.note_tests(_iter_tests(suite))
            except Exception as exc:  # noqa: BLE001  # a bug here must never break the suite
                record_error(exc, "test modules")
        return suite

    def _own_resultclass(self) -> bool:
        """Whether test_runner chooses its result class itself: Django gives it
        none, and its __init__ is not unittest's, which takes its class's."""
        return (
            super().get_resultclass() is None
            and getattr(self.test_runner, "__init__", None)
            is not unittest.TextTestRunner.__init__
        )

    def get_resultclass(self) -> Any:
        base = super().get_resultclass()
        if self._queryguard_parallel or self._queryguard_wrapped:
            return base
        if base is None:
            # the result class of the test runner (test_runner), not unittest's
            base = getattr(self.test_runner, "resultclass", None)
        return _guarded(base or unittest.TextTestResult)
