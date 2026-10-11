from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import ORMExecuteState, Session
from sqlalchemy.pool import Pool

from . import catalog, collector, indexes
from .callsite import find_callsite
from .events import RelationshipLoadEvent, StatementEvent

_installed = False
_index_installed = False
_error: BaseException | None = None
_local = threading.local()


def internal_error() -> BaseException | None:
    """The first exception swallowed by a listener, if any."""
    return _error


def _set_pending(name: str | None) -> None:
    _local.pending = None if name is None else (collector.generation(), name)


def _take_pending() -> str | None:
    pending = getattr(_local, "pending", None)
    _local.pending = None
    if pending is not None and pending[0] == collector.generation():
        return str(pending[1])
    return None


def _reset_for_tests() -> None:
    global _error
    _error = None
    _local.pending = None


def _on_orm_execute(state: ORMExecuteState) -> None:
    if _error is not None:
        return
    try:
        targets = collector.active()
        if not targets:
            return
        if not state.is_relationship_load:
            _set_pending(None)
            return
        name = str(state.loader_strategy_path[-1])  # type: ignore[index]
        parent = state.lazy_loaded_from
        key: object = None
        if parent is not None:
            key = parent.identity_key
            if key is None:
                key = id(parent)
        _set_pending(name)
        site = find_callsite()
        load = RelationshipLoadEvent(name, key, site)
        for target in targets:
            target.relationship_loads.append(load)
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        _fail(exc)


def _on_cursor_execute(
    conn: Any,
    cursor: Any,
    statement: str,
    parameters: Any,
    context: Any,
    executemany: bool,
) -> None:
    if _error is not None:
        return
    try:
        targets = collector.active()
        if not targets:
            return
        stmt = StatementEvent(statement, find_callsite(), _take_pending())
        for target in targets:
            target.statements.append(stmt)
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        _fail(exc)


def _on_after_cursor_execute(
    conn: Any,
    cursor: Any,
    statement: str,
    parameters: Any,
    context: Any,
    executemany: bool,
) -> None:
    if _error is not None:
        return
    try:
        # Every statement, in fixtures too: DDL clears the catalog cache.
        catalog.observe(conn, statement)
        targets = [target for target in collector.active() if target.index_check]
        if targets:
            indexes.check(conn, statement, executemany, targets)
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        _fail(exc)


def _guarded(handler: Callable[..., None]) -> Callable[..., None]:
    """A transaction or pool listener that never raises into the user's code."""

    def listener(*args: Any) -> None:
        if _error is not None:
            return
        try:
            handler(*args)
        except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
            _fail(exc)

    return listener


_on_commit = _guarded(catalog.on_commit)
_on_rollback = _guarded(catalog.on_rollback)
_on_rollback_savepoint = _guarded(catalog.on_rollback_savepoint)
_on_invalidate = _guarded(catalog.on_invalidate)


def _fail(exc: Exception) -> None:
    global _error
    if _error is None:
        _error = exc


def record_error(exc: Exception) -> None:
    """Disable measurement after a bug outside the listeners (first error wins)."""
    _fail(exc)


def swap_error(new: BaseException | None) -> BaseException | None:
    """Replace the recorded error and return the previous one.

    The plugin clears it for each session and restores the outer session's
    error afterwards, so in-process nested sessions stay independent.
    """
    global _error
    old, _error = _error, new
    return old


def install() -> None:
    """Register the class-level listeners once per process."""
    global _installed
    if _installed:
        return
    event.listen(Session, "do_orm_execute", _on_orm_execute)
    event.listen(Engine, "before_cursor_execute", _on_cursor_execute)
    _installed = True


def install_index_check() -> None:
    """Register the missing-index listener once per process.

    Separate from install(), so a session without the check pays nothing for
    it on each statement.
    """
    global _index_installed
    if _index_installed:
        return
    event.listen(Engine, "after_cursor_execute", _on_after_cursor_execute)
    # The ends of transactions that ran DDL clear the catalog cache.
    event.listen(Engine, "commit", _on_commit)
    event.listen(Engine, "rollback", _on_rollback)
    event.listen(Engine, "rollback_savepoint", _on_rollback_savepoint)
    event.listen(Pool, "invalidate", _on_invalidate)
    _index_installed = True
