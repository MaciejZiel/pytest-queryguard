from __future__ import annotations

import inspect

import pytest

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


def test_callsite_found_through_parent_greenlet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import contextlib
    import importlib

    from pytest_queryguard import callsite

    greenlet = importlib.import_module("greenlet")
    found: list[object] = []
    original = callsite._first_external

    def spy(frame: object) -> object:
        site = original(frame)  # type: ignore[arg-type]
        found.append(site)
        return site

    monkeypatch.setattr(callsite, "_first_external", spy)
    # ExitStack calls find_callsite from a stdlib frame, so the child
    # greenlet's own stack has no user frame and the parent must be searched.
    stack = contextlib.ExitStack()
    stack.callback(find_callsite)
    greenlet.greenlet(stack.close).switch()
    assert found[0] is None
    site = found[-1]
    assert site is not None
    assert site.function == "test_callsite_found_through_parent_greenlet"  # type: ignore[attr-defined]


def test_callsite_greenlet_lookup_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types

    broken = types.ModuleType("greenlet")  # no getcurrent(): AttributeError
    monkeypatch.setitem(sys.modules, "greenlet", broken)
    from pytest_queryguard import callsite

    assert list(callsite._parent_greenlet_frames()) == []
