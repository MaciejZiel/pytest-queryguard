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

import sys

from . import collector

_index_check = False
_wanted = False  # install() ran: hook every ORM in as soon as it is there
_strict_users = 0  # enable_strict() calls not undone yet
_cartesian_users = 0  # enable_cartesian() calls not undone yet
# the sqlalchemy module the listeners were installed for
_sqlalchemy_for: object = None


def install() -> None:
    """Install the ORM integrations; idempotent and cheap after the first call."""
    global _wanted
    _wanted = True
    collector.on_push(_on_push)
    collector.on_push_local(_on_push_local)
    collector.on_idle(_on_idle)
    _on_push()


def install_index_check() -> None:
    """Also check statements for missing indexes, in every integration."""
    global _index_check
    _index_check = True
    if _sqlalchemy_for is not None:
        from . import listeners

        listeners.install_index_check()
    _on_push()


def ready() -> None:
    """Hook in what the project has imported by now (after collection)."""
    if _wanted:
        _on_push()


def enable_strict() -> None:
    """Strict mode for SQLAlchemy too; calls nest, see ``disable_strict()``."""
    global _strict_users
    _strict_users += 1
    if _sqlalchemy_for is not None:
        from . import listeners

        listeners.enable_strict()


def disable_strict() -> None:
    global _strict_users
    if _strict_users == 0:
        return
    _strict_users -= 1
    if _sqlalchemy_for is not None:
        from . import listeners

        listeners.disable_strict()


def enable_cartesian() -> None:
    """The cartesian-load check (SQLAlchemy only); calls nest."""
    global _cartesian_users
    _cartesian_users += 1
    if _sqlalchemy_for is not None:
        from . import cartesian

        cartesian.enable()


def disable_cartesian() -> None:
    global _cartesian_users
    if _cartesian_users == 0:
        return
    _cartesian_users -= 1
    if _sqlalchemy_for is not None:
        from . import cartesian

        cartesian.disable()


def _sqlalchemy() -> None:
    """Install SQLAlchemy's listeners once the project has imported it.

    Never imported here otherwise: a project that only uses Django does not
    pay for importing SQLAlchemy. Its listeners are class-level, so engines,
    sessions and mappers made before they are installed are seen too; they
    are installed at the latest when collection ends, before any fixture.
    """
    global _sqlalchemy_for
    module = sys.modules.get("sqlalchemy")
    if module is None or module is _sqlalchemy_for:
        return
    from . import listeners

    listeners.install()
    if _index_check:
        listeners.install_index_check()
    if _sqlalchemy_for is None:
        for _ in range(_strict_users):
            listeners.enable_strict()
        if _cartesian_users:
            from . import cartesian

            for _ in range(_cartesian_users):
                cartesian.enable()
    _sqlalchemy_for = module


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
