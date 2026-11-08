"""backends: how the SQLAlchemy listeners are installed next to Django.

In-process, with a stand-in for the listener modules: this suite runs with
Django and measures its own coverage of backends.py (the "django" CI job),
while the SQLAlchemy side is tested end to end in tests/test_lazy_sqlalchemy.py,
in subprocesses.
"""

from __future__ import annotations

import importlib.machinery
import sys
import types
from typing import Any

import pytest

from pytest_queryguard import backends, errors


class _Fake:
    """A stand-in for ``listeners`` and ``cartesian``: records each call."""

    def __init__(self, calls: list[str], name: str, fail: bool = False) -> None:
        self._calls, self._name, self._fail = calls, name, fail

    def __getattr__(self, attr: str) -> Any:
        def call() -> None:
            if self._fail:
                raise RuntimeError("boom")
            self._calls.append(f"{self._name}.{attr}")

        return call


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every counter and flag of backends restored after the test."""
    found: list[str] = []
    for name, value in (
        ("_index_check", False),
        ("_wanted", False),
        ("_strict_users", 0),
        ("_cartesian_users", 0),
        ("_sqlalchemy_installed", True),
        ("_watcher", None),
    ):
        monkeypatch.setattr(backends, name, value)
    monkeypatch.setattr(backends, "_module", lambda name: _Fake(found, name))
    monkeypatch.setattr(backends, "_on_push", lambda: found.append("push"))
    monkeypatch.setattr(errors, "_error", None)
    monkeypatch.setattr(errors, "_failure", None)
    return found


def test_strict_cartesian_and_index_check_reach_installed_listeners(
    calls: list[str],
) -> None:
    backends.enable_strict()
    backends.disable_strict()
    backends.disable_strict()  # one more than enabled: nothing
    backends.enable_cartesian()
    backends.disable_cartesian()
    backends.disable_cartesian()
    backends.install_index_check()
    assert calls == [
        "listeners.enable_strict",
        "listeners.disable_strict",
        "cartesian.enable",
        "cartesian.disable",
        "listeners.install_index_check",
        "push",
    ]


def test_strict_and_cartesian_wait_for_sqlalchemy(
    calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backends, "_sqlalchemy_installed", False)
    backends.enable_strict()
    backends.enable_cartesian()
    assert calls == []
    assert (backends._strict_users, backends._cartesian_users) == (1, 1)


def test_a_late_install_catches_up_with_what_was_switched_on(
    calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backends, "_sqlalchemy_installed", False)
    monkeypatch.setitem(sys.modules, "sqlalchemy", types.ModuleType("sqlalchemy"))
    backends.enable_strict()
    backends.enable_cartesian()
    backends.install_index_check()
    calls.clear()
    backends._sqlalchemy()
    backends._sqlalchemy()  # once only
    assert calls == [
        "listeners.install",
        "listeners.install_index_check",
        "listeners.enable_strict",
        "cartesian.enable",
    ]


def test_a_failed_install_stops_measuring_and_is_not_retried(
    calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backends, "_sqlalchemy_installed", False)
    monkeypatch.setitem(sys.modules, "sqlalchemy", types.ModuleType("sqlalchemy"))
    monkeypatch.setattr(backends, "_module", lambda name: _Fake(calls, name, True))
    backends._sqlalchemy()
    failure = errors.internal_failure()
    assert failure is not None
    assert failure.source == "SQLAlchemy listeners (install)"
    assert backends._sqlalchemy_installed


class _InnerLoader:
    def __init__(self) -> None:
        self.ran: list[str] = []

    def create_module(self, spec: Any) -> None:
        return None

    def exec_module(self, module: types.ModuleType) -> None:
        self.ran.append(module.__name__)

    def get_resource_reader(self, name: str) -> str:
        return "reader"


def test_the_import_hook_installs_after_the_import_and_leaves_no_trace(
    calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backends, "_wanted", True)
    monkeypatch.setattr(backends, "_sqlalchemy", lambda: calls.append("install"))
    inner = _InnerLoader()
    loader = backends._Loader(inner)
    spec = importlib.machinery.ModuleSpec("sqlalchemy", loader)
    module = types.ModuleType("sqlalchemy")
    module.__spec__ = spec
    assert loader.create_module(spec) is None
    loader.exec_module(module)
    assert inner.ran == ["sqlalchemy"]
    assert calls == ["install"]
    assert getattr(module, "__loader__") is inner  # noqa: B009
    assert getattr(spec, "loader") is inner  # noqa: B009
    assert loader.get_resource_reader("x") == "reader"  # the rest is the inner's


def test_the_import_hook_waits_while_a_module_of_the_plugin_imports(
    calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backends, "_wanted", True)
    monkeypatch.setattr(backends, "_sqlalchemy", lambda: calls.append("install"))
    own = types.ModuleType("pytest_queryguard._half_imported")
    own.__spec__ = importlib.machinery.ModuleSpec(own.__name__, None)
    own.__spec__._initializing = True  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, own.__name__, own)
    assert backends._importing_own_module()
    module = types.ModuleType("sqlalchemy")
    module.__spec__ = None
    backends._Loader(_InnerLoader()).exec_module(module)
    assert calls == []


def test_the_finder_wraps_only_sqlalchemys_loader_and_removes_itself(
    calls: list[str],
) -> None:
    backends._watch_for_sqlalchemy()
    watcher = backends._watcher
    assert watcher is not None
    assert sys.meta_path[0] is watcher
    try:
        backends._watch_for_sqlalchemy()  # once
        assert sys.meta_path.count(watcher) == 1
        assert watcher.find_spec("not_sqlalchemy", None) is None
        spec = watcher.find_spec("sqlalchemy", None)
        assert spec is not None
        assert isinstance(spec.loader, backends._Loader)
        assert watcher not in sys.meta_path
        assert backends._watcher is None
        # not found where it was looked for: left to the other finders
        assert watcher.find_spec("sqlalchemy", ["/nonexistent-qg"]) is None
    finally:
        backends._unwatch()
        if watcher in sys.meta_path:
            sys.meta_path.remove(watcher)
    backends._unwatch()  # without a watcher: nothing
