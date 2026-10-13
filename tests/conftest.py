from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

pytest_plugins = ["pytester"]


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Iterator[None]:
    """Run the pytester sessions in file order even if pytest-randomly is installed.

    The files these tests write depend on their order (a table one test makes,
    a fixture that runs first), so shuffling them is not what is under test.
    The outer run keeps its own order. Pytester clears PYTEST_ADDOPTS when it
    is set up, so this is done after that, around the call.
    """
    if "pytester" not in getattr(item, "fixturenames", ()):
        return (yield)
    before = os.environ.get("PYTEST_ADDOPTS")
    os.environ["PYTEST_ADDOPTS"] = "-p no:randomly"
    try:
        return (yield)
    finally:
        if before is None:
            os.environ.pop("PYTEST_ADDOPTS", None)
        else:
            os.environ["PYTEST_ADDOPTS"] = before
