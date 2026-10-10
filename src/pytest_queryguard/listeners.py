from __future__ import annotations

import threading
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import ORMExecuteState, Session

from . import collector
from .callsite import find_callsite
from .events import RelationshipLoadEvent, StatementEvent

_installed = False
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
    except Exception as exc:  # noqa: BLE001
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
    except Exception as exc:  # noqa: BLE001
        _fail(exc)


def _fail(exc: Exception) -> None:
    global _error
    if _error is None:
        _error = exc


def install() -> None:
    """Register the class-level listeners once per process."""
    global _installed
    if _installed:
        return
    event.listen(Session, "do_orm_execute", _on_orm_execute)
    event.listen(Engine, "before_cursor_execute", _on_cursor_execute)
    _installed = True
