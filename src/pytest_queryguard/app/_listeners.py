"""The in-app listeners: per engine, writing to the current request only.

They mirror the pytest listeners in ``listeners.py`` but read the collector
from a ``ContextVar`` instead of the process-wide stack, and keep their own
error state. Registered only by ``watch()``; nothing runs before that.
"""

from __future__ import annotations

import contextvars
import threading
import time
import weakref
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import ORMExecuteState, Session

from ..callsite import find_callsite, find_flush
from ..collector import QueryCollector
from ..events import (
    CAUSE_DEFERRED,
    CAUSE_EXPIRED,
    CAUSE_REFRESH,
    ColumnLoadEvent,
    RelationshipLoadEvent,
    StatementEvent,
)
from ..listeners import _is_write, scope_of_connection, scope_of_session
from . import _core

# The relationship or column load of the ORM execution whose statement comes
# next, with the collector it was seen for. Context-local, so concurrent tasks
# of one request cannot take each other's. A read on an unwatched engine drops
# it (_on_unwatched_cursor_execute), so it is not credited to the next
# statement on a watched one.
_Pending = tuple[QueryCollector, RelationshipLoadEvent | None, ColumnLoadEvent | None]
_pending: contextvars.ContextVar[_Pending | None] = contextvars.ContextVar(
    "queryguard_app_pending", default=None
)
_TIMING_ATTR = "_queryguard_app_timing"
_SCOPE_ATTR = "_queryguard_scope"  # the attribute listeners.py tags with


def _column_load(state: ORMExecuteState) -> ColumnLoadEvent | None:
    """Mirror of listeners._record_column_load, without its side effects."""
    instance = getattr(state.load_options, "_refresh_state", None)
    if instance is None:
        return None
    options: Any = getattr(state.statement, "_compile_options", None)
    if not hasattr(options, "_only_load_props"):
        raise AttributeError(
            "SQLAlchemy's column-load compile options have no _only_load_props"
        )
    only = options._only_load_props
    expired = instance.expired_attributes
    columns = {prop.key for prop in instance.mapper.column_attrs}
    if only is None:
        cause, names = CAUSE_REFRESH, ()
    elif instance.expired or (only and set(only) <= expired):
        cause, names = CAUSE_EXPIRED, ()
    else:
        cause = CAUSE_DEFERRED
        names = tuple(sorted(set(only) & columns or only))
    key = instance.key if instance.key is not None else id(instance)
    return ColumnLoadEvent(
        instance.mapper.class_.__name__,
        names,
        cause,
        key,
        find_callsite(),
        scope=scope_of_session(state.session),
    )


def _on_orm_execute(state: ORMExecuteState) -> None:
    # No watched engine: the last one was collected without unwatch().
    if not _watched:
        return
    active = _core.live()
    if active is None or _core._error is not None:
        return
    try:
        collector = active.collector
        if state.is_column_load:
            load = _column_load(state)
            _pending.set((collector, None, load) if load is not None else None)
            return
        if not state.is_relationship_load:
            if _pending.get() is not None:
                _pending.set(None)
            return
        prop = state.loader_strategy_path[-1]  # type: ignore[index]
        parent = state.lazy_loaded_from
        key: object = None
        if parent is not None:
            key = parent.identity_key
            if key is None:
                key = id(parent)
        rel = RelationshipLoadEvent(
            str(prop),
            key,
            find_callsite(),
            getattr(prop, "uselist", None) is False,
            scope_of_session(state.session),
        )
        _pending.set((collector, rel, None))
    except Exception as exc:  # noqa: BLE001  # never break the application
        _core.fail(exc, "the do_orm_execute listener")


def _on_after_begin(session: Session, transaction: Any, connection: Any) -> None:
    """Tag the connection with its Session, for repeat_scope="session"."""
    if not _watched or _core.live() is None or _core._error is not None:
        return
    try:
        scope = scope_of_session(session)
        if scope is not None:
            setattr(connection, _SCOPE_ATTR, scope)
    except Exception as exc:  # noqa: BLE001  # never break the application
        _core.fail(exc, "the after_begin listener")


def _on_before_cursor_execute(
    conn: Any,
    cursor: Any,
    statement: str,
    parameters: Any,
    context: Any,
    executemany: bool,
) -> None:
    active = _core.live()
    if active is None or _core._error is not None:
        return
    try:
        collector = active.collector
        rel: RelationshipLoadEvent | None = None
        col: ColumnLoadEvent | None = None
        # As in listeners.py: an autoflush write may come between a load and
        # its SELECT, so a write leaves the pending load for the next read.
        write = _is_write(statement)
        if not write:
            pending = _pending.get()
            if pending is not None:
                _pending.set(None)
                if pending[0] is collector:
                    rel, col = pending[1], pending[2]
        # Loads are kept only when their statement reaches a watched engine.
        if rel is not None:
            collector.add_relationship_load(rel)
        if col is not None:
            col.sql = statement
            collector.add_column_load(col)
        stmt = StatementEvent(
            statement,
            find_callsite(),
            rel.relationship if rel is not None else None,
            column_load=col is not None,
            parameters=None if executemany else parameters,
            executemany=bool(executemany),
            flush=find_flush() if write else None,
            scope=scope_of_connection(conn),
        )
        collector.add_statement(stmt)
        if context is not None:
            setattr(context, _TIMING_ATTR, (stmt, collector, time.perf_counter()))
    except Exception as exc:  # noqa: BLE001  # never break the application
        _core.fail(exc, "the before_cursor_execute listener")


def _on_unwatched_cursor_execute(
    conn: Any,
    cursor: Any,
    statement: str,
    parameters: Any,
    context: Any,
    executemany: bool,
) -> None:
    """Drop the pending load when its SELECT runs on an unwatched engine.

    Registered on every Engine, so the engine a load runs on need not be
    guessed with ``Session.get_bind()``: an override of it, such as a
    ``ShardedSession``'s ``shard_chooser``, is application code.
    """
    if _pending.get() is None or _is_write(statement):
        return
    if not _in_watched(getattr(conn, "engine", None)):
        _pending.set(None)


def _on_after_cursor_execute(
    conn: Any,
    cursor: Any,
    statement: str,
    parameters: Any,
    context: Any,
    executemany: bool,
) -> None:
    now = time.perf_counter()
    if _core._error is not None:
        return
    try:
        timing = getattr(context, _TIMING_ATTR, None)
        if timing is None:
            return
        setattr(context, _TIMING_ATTR, None)
        stmt, collector, started = timing
        stmt.duration = now - started
        collector.add_db_time(stmt.duration)
    except Exception as exc:  # noqa: BLE001  # never break the application
        _core.fail(exc, "the after_cursor_execute listener")


# --- watch / unwatch -------------------------------------------------------

_lock = threading.Lock()
_watched: weakref.WeakSet[Engine] = weakref.WeakSet()
_session_listeners = False


def _in_chain(engine: Any, ancestor: Engine) -> bool:
    """Whether ``engine`` is ``ancestor`` or was derived from it.

    ``engine.execution_options(...)`` returns an ``OptionEngine`` that shares
    its parent's listeners; ``_proxied`` leads back to that parent.
    """
    while engine is not None:
        if engine is ancestor:
            return True
        engine = getattr(engine, "_proxied", None)
    return False


def _in_watched(engine: Any) -> bool:
    """Whether ``engine`` or an engine it was derived from is watched."""
    while engine is not None:
        if engine in _watched:
            return True
        engine = getattr(engine, "_proxied", None)
    return False


def _sync_engine(engine: Any) -> Engine:
    if isinstance(engine, Engine):
        return engine
    sync = getattr(engine, "sync_engine", None)  # AsyncEngine
    if isinstance(sync, Engine):
        return sync
    raise TypeError(
        f"watch() takes a SQLAlchemy Engine or AsyncEngine, got {type(engine).__name__}"
    )


def watch(engine: Any) -> None:
    """Measure the statements of ``engine`` in requests and ``track()`` blocks.

    Takes an ``Engine`` or an ``AsyncEngine``; calling it again for the same
    engine does nothing. Call it at startup, before the engine hands out
    connections: a ``Connection`` opened earlier does not see the listeners.
    An engine made with ``engine.execution_options()`` shares its parent's
    listeners: watching the parent covers it, and watching the parent after
    it moves its listeners to the parent.
    """
    sync = _sync_engine(engine)
    with _lock:
        _drop_session_listeners_if_unused()
        if _in_watched(sync):
            return
        # Engines derived from this one would run both sets of listeners.
        for derived in [e for e in _watched if _in_chain(e, sync)]:
            _remove_engine_listeners(derived)
        event.listen(sync, "before_cursor_execute", _on_before_cursor_execute)
        event.listen(sync, "after_cursor_execute", _on_after_cursor_execute)
        _watched.add(sync)
        _add_session_listeners()


def unwatch(engine: Any) -> None:
    """Stop measuring ``engine``; does nothing when it was not passed to watch().

    An engine watched through its parent (see ``watch()``) shares the parent's
    listeners and stops with ``unwatch(parent)``.
    """
    sync = _sync_engine(engine)
    with _lock:
        if sync in _watched:
            _remove_engine_listeners(sync)
        _drop_session_listeners_if_unused()


def _remove_engine_listeners(engine: Engine) -> None:
    event.remove(engine, "before_cursor_execute", _on_before_cursor_execute)
    event.remove(engine, "after_cursor_execute", _on_after_cursor_execute)
    _watched.discard(engine)


def _add_session_listeners() -> None:
    global _session_listeners
    if not _session_listeners:
        event.listen(Session, "do_orm_execute", _on_orm_execute)
        event.listen(Session, "after_begin", _on_after_begin)
        event.listen(Engine, "before_cursor_execute", _on_unwatched_cursor_execute)
        _session_listeners = True


def _drop_session_listeners_if_unused() -> None:
    """Remove the shared Session and Engine listeners once no engine is watched.

    Also after the last watched engine was garbage collected without
    ``unwatch()``; not from a finalizer, which could run in the middle of a
    SQLAlchemy event dispatch. Until then the listeners return at once.
    """
    global _session_listeners
    if not _watched and _session_listeners:
        event.remove(Session, "do_orm_execute", _on_orm_execute)
        event.remove(Session, "after_begin", _on_after_begin)
        event.remove(Engine, "before_cursor_execute", _on_unwatched_cursor_execute)
        _session_listeners = False


def is_watched(engine: Any) -> bool:
    """True while ``engine`` (or the sync engine of an AsyncEngine) is watched.

    An engine made with ``execution_options()`` is watched with its parent.
    """
    return _in_watched(_sync_engine(engine))
