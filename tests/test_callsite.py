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


def test_callsite_skips_stdlib_frames() -> None:
    import functools

    holder: list[object] = []
    functools.partial(lambda: holder.append(find_callsite()))()
    site = holder[0]
    assert site is not None
    assert site.filename.endswith("test_callsite.py")  # type: ignore[attr-defined]


def test_stdlib_files_are_internal() -> None:
    import json

    from pytest_queryguard.callsite import _is_internal

    assert _is_internal(json.__file__)
