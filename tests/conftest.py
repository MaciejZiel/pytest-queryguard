from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from . import dbredirect

pytest_plugins = ["pytester"]

# Before any test module imports create_engine: see tests/dbredirect.py.
if os.environ.get(dbredirect.ENV):
    dbredirect.install(os.environ[dbredirect.ENV])


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "sqlite_only(reason): the test is about SQLite itself; it keeps SQLite"
        " when QUERYGUARD_TEST_DB_URL moves the other tests to a server",
    )


@pytest.fixture(autouse=True)
def _sqlite_only(request: pytest.FixtureRequest) -> Iterator[None]:
    marker = request.node.get_closest_marker("sqlite_only")
    if marker is None or not dbredirect.active():
        yield
        return
    with dbredirect.paused():
        yield


# What every inner (pytester) session gets in PYTEST_ADDOPTS, see below.
INNER_ADDOPTS = "-p no:randomly"
TEST_CODE_FAILS = "-o queryguard_test_code=fail"


@pytest.fixture(autouse=True)
def _pytester_sessions_keep_their_order(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run the pytester sessions in file order even if pytest-randomly is installed.

    The files these tests write depend on their order (a table one test makes,
    a fixture that runs first), so shuffling them is not what is under test.
    The outer run keeps its own order. Pytester clears PYTEST_ADDOPTS when it is
    set up, so it is requested first. This is a fixture and not a hook wrapper
    because a wrapper adds frames to the stack the tests run on, and some of
    them probe the recursion limit.

    CI and BUILD_NUMBER are removed for the same reason: pytest prints every
    failure message in full in the short summary when one is set, so an
    inner session's output would differ between CI and a local run.

    The inner sessions also report the N+1s of test code
    (``queryguard_test_code = fail``): most of these tests write the loop
    under test in the test function itself, as the shortest way to make one.
    A test of the default (``off``) sets ``INNER_ADDOPTS`` on its own.
    """
    if "pytester" in request.fixturenames:
        request.getfixturevalue("pytester")
        monkeypatch.setenv("PYTEST_ADDOPTS", INNER_ADDOPTS + " " + TEST_CODE_FAILS)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("BUILD_NUMBER", raising=False)
