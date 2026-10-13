"""Memory over long runs and many engines (stability plan S6).

The two whole-session runs need ``QUERYGUARD_STRESS=1`` (set by the xdist CI
job): 10,000 tests with 20 statements each, and 2,000 tests that each create
and dispose an engine with the index check on. Each inner process stays far
below 1 GB; the peak is checked.
"""

from __future__ import annotations

import gc
import os
import re
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text

from pytest_queryguard import catalog, listeners, record_queries

MODELS = (Path(__file__).parent / "models.py").read_text()

stress = pytest.mark.skipif(
    os.environ.get("QUERYGUARD_STRESS") != "1",
    reason="set QUERYGUARD_STRESS=1 to run the long memory runs",
)

CONFTEST = """
import gc, sys, tracemalloc
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
        tracemalloc.stop()
        if sys.platform != "win32":
            import resource
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if sys.platform == "darwin":
                peak //= 1024
            print("PEAK_KB", peak)
"""

MANY_STATEMENTS = """
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

ENGINE = create_engine("sqlite://", poolclass=StaticPool)


@pytest.mark.allow_nplusone
@pytest.mark.parametrize("i", range({count}))
def test_many(i):
    with ENGINE.connect() as conn:
        for j in range(20):
            conn.execute(text(f"SELECT {{j}} + :i"), {{"i": i}})
"""

ENGINE_PER_TEST = """
import pytest
from sqlalchemy import create_engine, text


@pytest.mark.allow_nplusone
@pytest.mark.parametrize("i", range({count}))
def test_engine(i):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, v INTEGER)"))
        conn.execute(text("CREATE INDEX ix_t_v ON t (v)"))
        for j in range(5):
            conn.execute(text("SELECT * FROM t WHERE v = :v"), {{"v": j}})
    engine.dispose()
"""


def _measure(pytester: pytest.Pytester, count: int, *flags: str) -> tuple[int, int]:
    result = pytester.runpytest_subprocess(
        "-p", "no:cacheprovider", "-p", "no:randomly", "-q", "-s", *flags, timeout=900
    )
    result.assert_outcomes(passed=count)
    out = result.stdout.str()
    assert "QueryGuardInternalWarning" not in out
    growth = re.search(r"GROWTH (-?\d+)", out)
    peak = re.search(r"PEAK_KB (\d+)", out)
    assert growth, out[-1000:]
    return int(growth[1]), int(peak[1]) if peak else 0


@stress
def test_10000_tests_of_20_statements_keep_memory_flat(
    pytester: pytest.Pytester,
) -> None:
    count = 10_000
    pytester.makeconftest(CONFTEST.format(first=1_000, last=count - 1))
    pytester.makepyfile(test_many=MANY_STATEMENTS.format(count=count))
    without, _ = _measure(pytester, count, "-p", "no:queryguard")
    with_plugin, peak_kb = _measure(pytester, count)
    print(f"growth without {without}, with {with_plugin}, peak {peak_kb} KB")
    # 180,000 measured statements; the plugin may keep nothing per statement
    # or per test (pytest itself keeps a little per test, in both runs).
    assert with_plugin - without < 500_000, (with_plugin, without)
    if sys.platform != "win32":
        assert peak_kb < 1_000_000, f"peak {peak_kb} KB"


@stress
def test_an_engine_per_test_with_the_index_check_keeps_memory_flat(
    pytester: pytest.Pytester,
) -> None:
    count = 2_000
    pytester.makeconftest(CONFTEST.format(first=500, last=count - 1))
    pytester.makepyfile(test_engines=ENGINE_PER_TEST.format(count=count))
    without, _ = _measure(pytester, count, "-p", "no:queryguard")
    with_plugin, peak_kb = _measure(pytester, count, "--queryguard-index-check")
    print(f"growth without {without}, with {with_plugin}, peak {peak_kb} KB")
    assert with_plugin - without < 500_000, (with_plugin, without)
    if sys.platform != "win32":
        assert peak_kb < 1_000_000, f"peak {peak_kb} KB"


@pytest.mark.allow_nplusone  # 300 engines run the same statements on purpose
def test_many_engines_at_once_are_measured_and_released() -> None:
    gc.collect()
    cached_before = catalog.cached_engines()
    engines = [create_engine("sqlite://") for _ in range(300)]
    with record_queries() as log:
        for i, engine in enumerate(engines):
            with engine.connect() as conn:
                conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY)"))
                conn.execute(
                    select(text("id")).select_from(text("t")).where(text("id = :i")),
                    {"i": i},
                )
    assert log.count == 600
    assert listeners.internal_error() is None
    for engine in engines:
        catalog.for_engine(engine)  # what the index check caches per engine
    assert catalog.cached_engines() >= cached_before + 300
    for engine in engines:
        engine.dispose()
    del engines, engine, conn
    gc.collect()
    # the catalog cache holds engines weakly: none of the 300 is kept
    assert catalog.cached_engines() <= cached_before
