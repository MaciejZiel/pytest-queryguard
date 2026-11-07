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

from . import collector, listeners

_index_check = False


def install() -> None:
    """Install the ORM integrations; idempotent and cheap after the first call."""
    listeners.install()
    collector.on_push(_on_push)
    _on_push()


def install_index_check() -> None:
    """Also check statements for missing indexes, in every integration."""
    global _index_check
    listeners.install_index_check()
    _index_check = True
    _on_push()


def _on_push() -> None:
    if "django.conf" not in sys.modules:
        return
    from . import django_backend

    django_backend.ensure(index_check=_index_check)
