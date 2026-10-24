"""An internal error is a warning, also when warnings are turned into errors.

Many projects run with ``-W error`` or ``filterwarnings = error``. The plugin's
own bugs must still never fail a test: ``QueryGuardInternalWarning`` is shown
and recorded, never raised.
"""

from __future__ import annotations

import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from pytest_queryguard import QueryGuardInternalWarning, eager, listeners
from pytest_queryguard.api import record_queries
from pytest_queryguard.warnings import warn_internal

# pytester runs sessions in-process, so the outer plugin sees their queries too.
pytestmark = pytest.mark.allow_nplusone

MODELS = (Path(__file__).parent / "models.py").read_text()

BROKEN_CALLSITE = """
import pytest
from pytest_queryguard import listeners


@pytest.fixture(autouse=True)
def broken(monkeypatch):
    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(listeners, "find_callsite", boom)
"""

TESTS = """
from qgmodels import Customer, Order, make_sync_session


def test_loop():
    session = make_sync_session()
    for order in session.query(Order).all():
        _ = order.customer.name
    session.close()


def test_fails():
    session = make_sync_session()
    session.get(Customer, 1)
    session.close()
    assert False, "the user's own failure"
"""

INTERNAL = "error::pytest_queryguard.warnings.QueryGuardInternalWarning"


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makepyfile(qgmodels=MODELS)
    pytester.makeconftest(BROKEN_CALLSITE)
    pytester.makepyfile(test_a=TESTS)
    return pytester


@pytest.mark.parametrize("flag", [("-W", INTERNAL), ("-W", "error::UserWarning")])
def test_an_error_filter_does_not_fail_a_test(
    project: pytest.Pytester, flag: tuple[str, str]
) -> None:
    result = project.runpytest(*flag)
    result.assert_outcomes(passed=1, failed=1, warnings=1)
    result.stdout.fnmatch_lines(
        [
            "*QueryGuardInternalWarning*boom*",
            "*queryguard: measurement stopped*",
            "*FAILED test_a.py::test_fails - AssertionError: the user's own failure*",
        ]
    )
    assert "INTERNALERROR" not in result.stdout.str()


def test_an_error_filter_in_the_ini_does_not_fail_a_test(
    project: pytest.Pytester,
) -> None:
    project.makeini(f"[pytest]\nfilterwarnings =\n    {INTERNAL}\n")
    result = project.runpytest()
    result.assert_outcomes(passed=1, failed=1, warnings=1)


def test_an_error_in_the_summary_does_not_crash_the_session(
    project: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The first error happens after the tests, in the terminal summary.
    project.makeconftest("")
    from pytest_queryguard import plugin

    def broken_report(*args: object) -> None:
        raise RuntimeError("report broke")

    monkeypatch.setattr(plugin, "_write_report", broken_report)
    result = project.runpytest("--queryguard-report", "-W", INTERNAL)
    result.assert_outcomes(failed=2)  # the N+1 of test_loop was measured
    result.stdout.fnmatch_lines(["*report broke*", "*2 failed*"])
    assert "INTERNALERROR" not in result.stdout.str()
    assert "Traceback" not in result.stdout.str()


def test_an_ignore_filter_is_respected(project: pytest.Pytester) -> None:
    ignore = "ignore::pytest_queryguard.warnings.QueryGuardInternalWarning"
    result = project.runpytest("-W", ignore)
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.no_fnmatch_line("*QueryGuardInternalWarning*")


# ------------------------------------------------------------- unit level


def test_warn_internal_is_recorded_not_raised_under_an_error_filter() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("error")
        warn_internal(QueryGuardInternalWarning("broken"))
    assert [str(w.message) for w in caught] == ["broken"]
    assert caught[0].filename == __file__


def test_warn_internal_keeps_the_filters_it_found() -> None:
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("error")
        before = list(warnings.filters)
        warn_internal(QueryGuardInternalWarning("broken"))
        assert warnings.filters == before


@contextmanager
def _recorded_error() -> Iterator[None]:
    """Let the block record an internal error; restore the state afterwards.

    Inside the test body, so the plugin measuring this test never sees it.
    """
    previous = listeners.swap_error(None)
    try:
        yield
    finally:
        listeners.swap_error(previous)


def test_record_queries_does_not_raise_its_warning() -> None:
    engine = create_engine("sqlite://")
    with _recorded_error(), warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("error")
        with engine.connect() as conn, record_queries():
            listeners.record_error(RuntimeError("boom"))
            conn.execute(text("select 1"))
    engine.dispose()
    assert len(caught) == 1
    assert "may be incomplete" in str(caught[0].message)
    assert caught[0].filename == __file__


@pytest.fixture
def fresh_eager() -> Iterator[None]:
    eager._broken = False
    eager._warned = False
    yield
    eager._broken = False
    eager._warned = False


def test_the_eager_load_check_does_not_raise_into_a_listener(
    fresh_eager: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # _broke() runs inside SQLAlchemy's do_orm_execute: a raised warning would
    # reach the user's session.execute().
    def boom(options: object) -> list[object]:
        raise RuntimeError("boom")

    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from tests.models import Order, make_sync_session

    monkeypatch.setattr(eager, "_elements", boom)
    session = make_sync_session()
    eager.enable()
    tracker = eager.start()
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("error")
            rows = session.scalars(
                select(Order).options(selectinload(Order.customer))
            ).all()
    finally:
        eager.stop(tracker)
        eager.disable()
        session.close()
    assert rows
    assert eager._broken
    assert [type(w.message) for w in caught] == [QueryGuardInternalWarning]
