from __future__ import annotations

import pytest

pytest_plugins = ["pytester"]


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
    """
    if "pytester" in request.fixturenames:
        request.getfixturevalue("pytester")
        monkeypatch.setenv("PYTEST_ADDOPTS", "-p no:randomly")
