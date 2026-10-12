"""Memory growth and thread safety, standard library only."""

from __future__ import annotations

import gc
import re
import threading
import tracemalloc
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from pytest_queryguard import collector, listeners, plugin
from pytest_queryguard.collector import QueryCollector
from tests.models import Order, make_sync_session


@pytest.fixture
def measured(request: pytest.FixtureRequest) -> Iterator[None]:
    """Drive the listeners by hand; the plugin's own measurement stays off."""
    stash = request.config.stash
    saved = stash[plugin._settings]
    stash[plugin._settings] = plugin._Settings("off", 2, 5)
    listeners.install()
    collector._reset_for_tests()
    listeners._reset_for_tests()
    yield
    collector._reset_for_tests()
    listeners._reset_for_tests()
    stash[plugin._settings] = saved


# ----------------------------------------------------------------- memory


def _cycle(statements: int) -> None:
    """One "test": an engine, a collector with the default cap, many statements."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    rec = QueryCollector()
    rec.max_events = 100
    collector.push(rec)
    try:
        with engine.connect() as conn:
            for i in range(statements):
                conn.execute(text(f"select {i}"))
    finally:
        collector.pop(rec)
        engine.dispose()
    assert rec.statement_count == statements
    assert len(rec.statements) == 100


def test_state_does_not_grow_across_many_measurements(measured: None) -> None:
    for _ in range(5):  # warm up: caches, interned strings, first-call costs
        _cycle(200)
    gc.collect()
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        for _ in range(150):
            _cycle(200)  # 30000 statements over 150 collectors and engines
        gc.collect()
        growth = tracemalloc.get_traced_memory()[0] - before
    finally:
        tracemalloc.stop()
    # Nothing per-statement or per-test may be kept. The margin covers allocator
    # noise; a leak of even one event per measurement is far above it.
    assert growth < 150_000, f"{growth} bytes kept after 30000 statements"
    assert collector.active() == ()


def test_one_collector_stores_at_most_its_cap(measured: None) -> None:
    rec = QueryCollector()
    rec.max_events = 50
    collector.push(rec)
    try:
        session = make_sync_session()
        for _ in range(3000):
            session.query(Order).first()
    finally:
        collector.pop(rec)
    assert rec.statement_count >= 3000
    assert len(rec.statements) == 50
    assert rec.truncated


INNER_CONFTEST = """
import gc, tracemalloc
import pytest

FIRST, LAST = {first}, {last}
_start = {{}}


@pytest.fixture(autouse=True)
def _memory(request):
    i = request.node.callspec.params["i"]
    if i == FIRST:
        gc.collect()
        tracemalloc.start()
        _start["bytes"] = tracemalloc.get_traced_memory()[0]
    yield
    if i == LAST:
        gc.collect()
        print("GROWTH", tracemalloc.get_traced_memory()[0] - _start["bytes"])
"""

INNER_TESTS = """
import pytest
from qgmodels import Order, make_sync_session

_session = make_sync_session()


@pytest.mark.allow_nplusone
@pytest.mark.parametrize("i", range({count}))
def test_many(i):
    for order in _session.query(Order).all():
        _ = order.customer  # relationship loads on every test
    _session.expire_all()
"""


def _growth(pytester: pytest.Pytester, *flags: str) -> int:
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-s", *flags)
    result.assert_outcomes(passed=2000)
    match = re.search(r"GROWTH (-?\d+)", result.stdout.str())
    assert match, result.stdout.str()[-500:]
    return int(match[1])


def test_plugin_adds_no_memory_growth_over_2000_tests(
    pytester: pytest.Pytester,
) -> None:
    # The same 2000 tests, with and without the plugin. pytest itself keeps a
    # few KB per test (reports, node ids); the plugin must add nothing visible.
    from pathlib import Path

    pytester.makepyfile(qgmodels=(Path(__file__).parent / "models.py").read_text())
    pytester.makeconftest(INNER_CONFTEST.format(first=200, last=1999))
    pytester.makepyfile(test_many=INNER_TESTS.format(count=2000))
    without = _growth(pytester, "-p", "no:queryguard")
    with_plugin = _growth(pytester)
    extra = with_plugin - without
    assert extra < 100_000, (
        f"{extra} bytes more with the plugin ({with_plugin} vs {without})"
    )


# ---------------------------------------------------------------- threads

THREADS = 6
PER_THREAD = 150


def _worker(errors: list[BaseException], barrier: threading.Barrier) -> None:
    try:
        engine = create_engine("sqlite://", poolclass=StaticPool)
        barrier.wait(timeout=30)
        with engine.connect() as conn:
            for i in range(PER_THREAD):
                conn.execute(text(f"select {i}"))
        engine.dispose()
    except BaseException as exc:  # noqa: BLE001  # reported to the main thread
        errors.append(exc)


def _run_threads() -> list[BaseException]:
    errors: list[BaseException] = []
    barrier = threading.Barrier(THREADS)
    threads = [
        threading.Thread(target=_worker, args=(errors, barrier)) for _ in range(THREADS)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads), "a thread hung"
    return errors


def test_statements_from_many_threads_are_all_counted(measured: None) -> None:
    rec = QueryCollector()
    collector.push(rec)
    try:
        errors = _run_threads()
    finally:
        collector.pop(rec)
    assert errors == []
    assert listeners.internal_error() is None
    assert rec.statement_count == THREADS * PER_THREAD


def test_collectors_can_come_and_go_while_threads_query(measured: None) -> None:
    errors: list[BaseException] = []
    barrier = threading.Barrier(THREADS + 1)
    threads = [
        threading.Thread(target=_worker, args=(errors, barrier)) for _ in range(THREADS)
    ]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=30)
    seen = 0
    while any(thread.is_alive() for thread in threads):
        rec = QueryCollector()
        collector.push(rec)
        collector.pop(rec)
        seen += rec.statement_count
    for thread in threads:
        thread.join(timeout=60)
    assert errors == []
    assert listeners.internal_error() is None
    assert collector.active() == ()
    assert 0 <= seen <= THREADS * PER_THREAD


def test_nested_collectors_each_see_every_thread_statement(measured: None) -> None:
    outer, inner = QueryCollector(), QueryCollector()
    collector.push(outer)
    collector.push(inner)
    try:
        errors = _run_threads()
    finally:
        collector.pop(inner)
        collector.pop(outer)
    assert errors == []
    assert outer.statement_count == inner.statement_count == THREADS * PER_THREAD
