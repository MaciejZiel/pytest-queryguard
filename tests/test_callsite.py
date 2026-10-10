from __future__ import annotations

import inspect

from pytest_queryguard.callsite import find_callsite


def test_callsite_points_to_test_file() -> None:
    site = find_callsite()
    expected = inspect.currentframe().f_lineno - 1  # type: ignore[union-attr]
    assert site is not None
    assert site.filename.endswith("test_callsite.py")
    assert site.lineno == expected
    assert site.function == "test_callsite_points_to_test_file"
