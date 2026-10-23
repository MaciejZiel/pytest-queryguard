"""Memory growth and thread safety, standard library only."""

from __future__ import annotations

import gc
import re
import sys
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


FREE_THREADED = not getattr(sys, "_is_gil_enabled", lambda: True)()
PLUGIN_ONLY = [tracemalloc.Filter(True, "*/pytest_queryguard/*", all_frames=True)]


def _snapshot() -> tracemalloc.Snapshot:
    gc.collect()
    return tracemalloc.take_snapshot()


def test_state_does_not_grow_across_many_measurements(measured: None) -> None:
    for _ in range(5):  # warm up: caches, interned strings, first-call costs
        _cycle(200)
    tracemalloc.start(12)
    try:
        before = _snapshot()
        for _ in range(150):
            _cycle(200)  # 30000 statements over 150 collectors and engines
        after = _snapshot()
    finally:
        tracemalloc.stop()
    total = sum(d.size_diff for d in after.compare_to(before, "filename"))
    diffs = after.filter_traces(PLUGIN_ONLY).compare_to(
        before.filter_traces(PLUGIN_ONLY), "filename"
    )
    owned = sum(d.size_diff for d in diffs)
    owned_blocks = sum(d.count_diff for d in diffs)
    # What the plugin allocated (any frame of the allocation in its package)
    # must not grow. Counted in blocks: a leak of even one object per
    # measurement keeps 150 of them. A few may stay for good: walking the
    # stack for the call site can create the frame objects of the pytest frames
    # above the test (3 blocks, 4 KB, seen on 3.14t), which live on until the
    # test returns. The byte bound catches one buffer that keeps growing.
    assert owned_blocks < 50, f"{owned_blocks} plugin-owned blocks kept"
    assert owned < 32_768, f"{owned} bytes of plugin-owned memory kept"
    # The free-threaded build frees SQLAlchemy's compiled-statement caches late
    # and out of step with the cycles (1 MB to 7 MB after 150 cycles, none of it
    # allocated by this package), so the whole-process figure says nothing about
    # the plugin there; the plugin-owned figure above is the check on that build.
    if not FREE_THREADED:
        assert total < 150_000, f"{total} bytes kept after 30000 statements"
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


@pytest.mark.parametrize("locked", [True, False])
def test_collector_counters_under_threads_on_both_code_paths(
    monkeypatch: pytest.MonkeyPatch, locked: bool
) -> None:
    """The lock path (free-threaded builds) and the plain path count alike."""
    if not locked and FREE_THREADED:
        pytest.skip("the unlocked path is the race itself without the GIL")
    from pytest_queryguard.events import (
        ColumnLoadEvent,
        RelationshipLoadEvent,
        StatementEvent,
    )

    monkeypatch.setattr(collector, "_FREE_THREADED", locked)
    rec = QueryCollector()
    rec.max_events = 50
    threads, per_thread = 8, 500

    def work() -> None:
        for _ in range(per_thread):
            rec.add_statement(StatementEvent("select 1", None, None))
            rec.add_relationship_load(RelationshipLoadEvent("A.b", 1, None))
            rec.add_column_load(ColumnLoadEvent("A", ("x",), "deferred", 1, None))
            rec.add_db_time(0.5)

    pool = [threading.Thread(target=work) for _ in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join()
    total = threads * per_thread
    assert rec.statement_count == total
    assert rec.relationship_load_count == total
    assert rec.column_load_count == total
    assert rec.db_time == total * 0.5
    # the lock path also keeps the cap exact
    if locked:
        assert len(rec.statements) == 50
        assert len(rec.relationship_loads) == 50
        assert len(rec.column_loads) == 50
