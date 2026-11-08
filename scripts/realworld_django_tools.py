"""A pytest plugin that runs django-zeal or nplusone around each test and records what it reports.

Used by .github/workflows/realworld.yml (``suite=django``) with
``-p realworld_django_tools -p no:queryguard``, to see what the other N+1
tools for Django report on the same test suites as pytest-queryguard. The
tool is ``REALWORLD_TOOL`` (``zeal`` or ``nplusone``); one JSON object per
report is appended to ``REALWORLD_TOOL_OUT``.

Each tool watches the test call only (not the fixtures), as pytest-queryguard
does, and reports without failing the test:

- django-zeal: its patches are installed as its ``AppConfig.ready()`` does
  (the project's settings are not changed), ``ZEAL_RAISE`` is set to False,
  and the call runs in ``zeal_context()``. Each report is its
  ``nplusone_detected`` signal. With ``ZEAL_RAISE = False`` zeal also warns
  (``UserWarning``); the workflow ignores that warning by its text, so that a
  project that turns warnings into errors does not fail on it.
- nplusone: ``import nplusone.ext.django`` patches Django, and the call runs
  with nplusone's listeners set up as its ``Profiler`` does, with a parent
  that records each message instead of raising.

A tool that does not import or set up records one ``setup-error`` line, and
then nothing runs under it. Standard library, pytest and the tool only.
"""

from __future__ import annotations

import json
import os
import traceback
from collections.abc import Generator
from typing import Any

import pytest

TOOL = os.environ.get("REALWORLD_TOOL", "")
_current = [""]
_ready = [False]


def _write(record: dict[str, Any]) -> None:
    path = os.environ.get("REALWORLD_TOOL_OUT")
    if not path:
        return
    # one short line per write in append mode, so pytest-xdist workers can
    # share the file
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"tool": TOOL, **record}, sort_keys=True) + "\n")


def _setup_zeal() -> None:
    from django.conf import settings
    from zeal.constants import initialize_app_registry
    from zeal.patch import patch
    from zeal.signals import nplusone_detected

    settings.ZEAL_RAISE = False
    initialize_app_registry()
    patch()

    def detected(sender: object, exception: BaseException, **kwargs: object) -> None:
        _write({"event": "report", "nodeid": _current[0], "message": str(exception)})

    nplusone_detected.connect(detected, weak=False)


def _setup_nplusone() -> None:
    import nplusone.ext.django  # noqa: F401  (patches Django's ORM)


class _Recorder:
    """nplusone's listeners call ``parent.notify(message)``; this records it."""

    def notify(self, message: Any) -> None:
        _write(
            {
                "event": "report",
                "nodeid": _current[0],
                "message": str(getattr(message, "message", message)),
            }
        )


def pytest_sessionstart(session: pytest.Session) -> None:
    # Here, not in pytest_configure: pytest-django (or the project's conftest)
    # has set Django up by now.
    try:
        if TOOL == "zeal":
            _setup_zeal()
        elif TOOL == "nplusone":
            _setup_nplusone()
        else:
            raise RuntimeError(f"REALWORLD_TOOL is {TOOL!r}, not zeal or nplusone")
    except Exception:  # noqa: BLE001  (any failure of the tool is data)
        _write({"event": "setup-error", "message": traceback.format_exc()[-2000:]})
        return
    _ready[0] = True


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, object, object]:
    _current[0] = item.nodeid
    if not _ready[0]:
        return (yield)
    if TOOL == "zeal":
        from zeal import zeal_context

        with zeal_context():
            return (yield)
    from nplusone.core import listeners

    started = {}
    recorder = _Recorder()
    for name, listener_type in listeners.listeners.items():
        started[name] = listener_type(recorder)
        started[name].setup()
    try:
        return (yield)
    finally:
        for listener in started.values():
            listener.teardown()
