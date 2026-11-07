"""The ORM integrations that feed the collector: SQLAlchemy, and Django when used.

Both record the same events (``events.py``) into the same collectors, so every
rule, report and file format works for either. SQLAlchemy's listeners are
class-level and are installed once (``listeners.install``). Django can only be
hooked once its settings are configured and its app registry is ready, which
under pytest-django may happen after this plugin's ``pytest_configure``, so
``install()`` also registers a check that runs at the start of every
measurement (``collector.on_push``) and hooks Django in as soon as it is ready.

Django is never imported here: the Django integration is loaded only once the
application has imported ``django.conf`` itself.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import sys
from collections.abc import Sequence
from types import ModuleType
from typing import Any

from . import collector, errors

_index_check = False
_wanted = False  # install() ran: hook every ORM in as soon as it is there
_strict_users = 0  # enable_strict() calls not undone yet
_cartesian_users = 0  # enable_cartesian() calls not undone yet
_sqlalchemy_installed = False


def _module(name: str) -> Any:
    # by name, as plugin._sqlalchemy_check does: one module object for both
    return importlib.import_module(f"pytest_queryguard.{name}")


def install() -> None:
    """Install the ORM integrations; idempotent and cheap after the first call."""
    global _wanted
    _wanted = True
    collector.on_push(_on_push)
    collector.on_push_local(_on_push_local)
    collector.on_idle(_on_idle)
    if "sqlalchemy" not in sys.modules and not _sqlalchemy_installed:
        _watch_for_sqlalchemy()
    _on_push()


def install_index_check() -> None:
    """Also check statements for missing indexes, in every integration."""
    global _index_check
    _index_check = True
    if _sqlalchemy_installed:
        _module("listeners").install_index_check()
    _on_push()


def ready() -> None:
    """Hook in what the project has imported by now (after collection)."""
    if _wanted:
        _on_push()


def enable_strict() -> None:
    """Strict mode for SQLAlchemy too; calls nest, see ``disable_strict()``."""
    global _strict_users
    _strict_users += 1
    if _sqlalchemy_installed:
        _module("listeners").enable_strict()


def disable_strict() -> None:
    global _strict_users
    if _strict_users == 0:
        return
    _strict_users -= 1
    if _sqlalchemy_installed:
        _module("listeners").disable_strict()


def enable_cartesian() -> None:
    """The cartesian-load check (SQLAlchemy only); calls nest."""
    global _cartesian_users
    _cartesian_users += 1
    if _sqlalchemy_installed:
        _module("cartesian").enable()


def disable_cartesian() -> None:
    global _cartesian_users
    if _cartesian_users == 0:
        return
    _cartesian_users -= 1
    if _sqlalchemy_installed:
        _module("cartesian").disable()


def _sqlalchemy() -> None:
    """Install SQLAlchemy's listeners once the project has imported it.

    Never imported here otherwise: a project that only uses Django does not
    pay for importing SQLAlchemy. Its listeners are class-level, so engines,
    sessions and mappers made before they are installed are seen too. They
    are installed as soon as ``import sqlalchemy`` finishes (_AfterImport),
    and else at the latest when collection ends or a measurement starts.
    Never raises: a failure stops measurement as a listener's would.
    """
    global _sqlalchemy_installed
    if _sqlalchemy_installed or "sqlalchemy" not in sys.modules:
        return
    _sqlalchemy_installed = True  # first: a failure is not retried per push
    try:
        listeners = _module("listeners")
        listeners.install()
        if _index_check:
            listeners.install_index_check()
        for _ in range(_strict_users):
            listeners.enable_strict()
        if _cartesian_users:
            cartesian = _module("cartesian")
            for _ in range(_cartesian_users):
                cartesian.enable()
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        errors.fail(exc, "SQLAlchemy listeners (install)")


class _AfterImport(importlib.abc.MetaPathFinder):
    """Installs the listeners right after ``import sqlalchemy`` runs.

    Also when that import is the first in a test's body or in a
    ``record_queries()`` block, after the measurement started. Leaves the
    import itself to the other finders and removes itself once used.
    """

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None,
        target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname != "sqlalchemy":
            return None
        _unwatch()
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.loader is None:
            return spec
        spec.loader = _Loader(spec.loader)
        return spec


class _Loader(importlib.abc.Loader):
    def __init__(self, loader: Any) -> None:
        self._loader = loader

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> Any:
        return self._loader.create_module(spec)

    def exec_module(self, module: ModuleType) -> None:
        # the module keeps its own loader, as if this one was never there
        module.__loader__ = self._loader
        if module.__spec__ is not None:
            module.__spec__.loader = self._loader
        self._loader.exec_module(module)
        if _wanted and not _importing_own_module():
            _sqlalchemy()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._loader, name)


def _importing_own_module() -> bool:
    """Whether a module of this package is being imported: then it is the
    one that imported SQLAlchemy (its _compat), and installing the listeners
    now would import it again half-initialised. The next measurement does."""
    for name, module in list(sys.modules.items()):
        if name.startswith("pytest_queryguard."):
            spec = getattr(module, "__spec__", None)
            if spec is not None and getattr(spec, "_initializing", False):
                return True
    return False


_watcher: _AfterImport | None = None


def _watch_for_sqlalchemy() -> None:
    global _watcher
    if _watcher is None:
        _watcher = _AfterImport()
        sys.meta_path.insert(0, _watcher)


def _unwatch() -> None:
    global _watcher
    if _watcher is not None:
        if _watcher in sys.meta_path:
            sys.meta_path.remove(_watcher)
        _watcher = None


def _on_push() -> None:
    if _wanted:
        _sqlalchemy()
    if "django.conf" not in sys.modules:
        return
    from . import django_backend

    django_backend.ensure(index_check=_index_check)


def _on_push_local() -> None:
    if _wanted:
        _sqlalchemy()
    django_backend = sys.modules.get("pytest_queryguard.django_backend")
    if django_backend is not None:
        django_backend.attach()


def _on_idle() -> None:
    django_backend = sys.modules.get("pytest_queryguard.django_backend")
    if django_backend is not None:
        django_backend.detach()
