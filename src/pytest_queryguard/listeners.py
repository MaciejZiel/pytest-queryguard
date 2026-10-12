from __future__ import annotations

import contextlib
import itertools
import os
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import ORMExecuteState, Session
from sqlalchemy.pool import Pool

from . import catalog, collector, indexes, mysql_index, sqlite_index
from .callsite import find_callsite, find_flush
from .events import (
    CAUSE_DEFERRED,
    CAUSE_EXPIRED,
    CAUSE_REFRESH,
    ColumnLoadEvent,
    RelationshipLoadEvent,
    StatementEvent,
)

_installed = False
_index_installed = False
_error: BaseException | None = None
_failure: Failure | None = None
_local = threading.local()
# Scope numbers for the repeat rules: one per Session, one per connection that
# no Session opened. Process-wide and never reused; the number is stored on the
# Session or Connection object, so it cannot be mixed up by a recycled id().
_scopes = itertools.count(1)
_SCOPE_ATTR = "_queryguard_scope"


@dataclass(frozen=True, slots=True)
class Failure:
    """What went wrong inside the plugin, for the warning and the summary."""

    source: str  # the listener or step that raised
    error: BaseException
    raised_at: str | None  # "file.py:12 in function", innermost frame

    @property
    def summary(self) -> str:
        return f"{type(self.error).__name__}: {self.error}"


def internal_error() -> BaseException | None:
    """The first exception swallowed by a listener, if any."""
    return _error


def internal_failure() -> Failure | None:
    """Which listener or step failed first, and why; None without an error.

    The error itself is the one ``internal_error()`` returns. When it was set
    without a source (``swap_error()``), the source is "unknown".
    """
    if _error is None:
        return None
    if _failure is not None and _failure.error is _error:
        return _failure
    return Failure("unknown", _error, _innermost(_error))


def _innermost(exc: BaseException) -> str | None:
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return None
    last = frames[-1]
    return f"{os.path.basename(last.filename)}:{last.lineno} in {last.name}"


def _scope_of(owner: Any) -> int | None:
    """The scope number of a Session or Connection, assigned on first use."""
    try:
        scope = owner.__dict__.get(_SCOPE_ATTR)
        if scope is None:
            scope = next(_scopes)
            setattr(owner, _SCOPE_ATTR, scope)
        return scope  # type: ignore[no-any-return]
    except (AttributeError, TypeError):
        return None


def scope_of_session(session: Any) -> int | None:
    """The scope of a Session: the same number for all its transactions."""
    return _scope_of(session)


def scope_of_connection(conn: Any) -> int | None:
    """The scope of a statement's connection.

    A connection a Session opened (see ``_on_after_begin``) belongs to that
    Session; any other connection is its own scope, and ``engine.connect()``
    gives a new one for each checkout.
    """
    return _scope_of(conn)


def _on_after_begin(session: Session, transaction: Any, connection: Any) -> None:
    """Tell the connection which Session it serves, for every transaction."""
    # Without the tag the connection counts as a scope of its own.
    with contextlib.suppress(AttributeError, TypeError):
        setattr(connection, _SCOPE_ATTR, scope_of_session(session))


def _set_pending(name: str | None, column_load: ColumnLoadEvent | None = None) -> None:
    if name is None and column_load is None:
        _local.pending = None
    else:
        _local.pending = (collector.generation(), name, column_load)


def _take_pending() -> tuple[str | None, ColumnLoadEvent | None]:
    """(relationship, column load) of the ORM execution the next statement belongs to."""
    pending = getattr(_local, "pending", None)
    _local.pending = None
    if pending is not None and pending[0] == collector.generation():
        return pending[1], pending[2]
    return None, None


def _reset_for_tests() -> None:
    global _error, _failure
    _error = None
    _failure = None
    _local.pending = None


def _record_column_load(
    state: ORMExecuteState, targets: tuple[collector.QueryCollector, ...]
) -> None:
    """Note an instance that loads columns it already has a row for."""
    instance = getattr(state.load_options, "_refresh_state", None)
    if instance is None:
        _set_pending(None)
        return
    options: Any = getattr(state.statement, "_compile_options", None)
    if not hasattr(options, "_only_load_props"):
        # Without it every load would look like a plain refresh, with a wrong
        # hint. Stop through the internal-error path instead of mislabeling.
        raise AttributeError(
            "SQLAlchemy's column-load compile options have no _only_load_props; "
            "this SQLAlchemy version is not supported by the column-load kind"
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
    load = ColumnLoadEvent(
        instance.mapper.class_.__name__,
        names,
        cause,
        key,
        find_callsite(),
        scope=scope_of_session(state.session),
    )
    _set_pending(None, column_load=load)
    for target in targets:
        target.add_column_load(load)


def _on_orm_execute(state: ORMExecuteState) -> None:
    if _error is not None:
        return
    try:
        targets = collector.active()
        if not targets:
            return
        if state.is_column_load:
            _record_column_load(state, targets)
            return
        if not state.is_relationship_load:
            _set_pending(None)
            return
        prop = state.loader_strategy_path[-1]  # type: ignore[index]
        name = str(prop)
        scalar = getattr(prop, "uselist", None) is False
        parent = state.lazy_loaded_from
        key: object = None
        if parent is not None:
            key = parent.identity_key
            if key is None:
                key = id(parent)
        _set_pending(name)
        site = find_callsite()
        load = RelationshipLoadEvent(
            name, key, site, scalar, scope_of_session(state.session)
        )
        for target in targets:
            target.add_relationship_load(load)
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        _fail(exc, "do_orm_execute listener (relationship loads)")


_WRITE_VERBS = frozenset(("INSERT", "UPDATE", "DELETE"))


def _is_write(statement: str) -> bool:
    # Slice first: only a statement that starts with whitespace needs lstrip().
    head = statement[:6]
    if head[:1].isspace():
        head = statement.lstrip()[:6]
    return head.upper() in _WRITE_VERBS


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
        # The ORM runs do_orm_execute before it autoflushes, so the first
        # statement after a lazy load may be the flush's write, not the load's
        # SELECT. Loads are reads: leave the pending attribution for the SELECT.
        write = _is_write(statement)
        if write:
            relationship, column_load = None, None
        else:
            relationship, column_load = _take_pending()
        if column_load is not None:
            column_load.sql = statement
        stmt = StatementEvent(
            statement,
            find_callsite(),
            relationship,
            column_load=column_load is not None,
            parameters=None if executemany else parameters,
            executemany=bool(executemany),
            flush=find_flush() if write else None,
            scope=scope_of_connection(conn),
        )
        for target in targets:
            target.add_statement(stmt)
        if context is not None:
            # Read back by the after listener; per execution, so statements
            # that overlap in time (asyncio tasks) never mix up. Taken last,
            # so the time spent finding the call site is not counted.
            context._queryguard_timing = (stmt, targets, time.perf_counter())
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        _fail(exc, "before_cursor_execute listener (statements)")


def _on_timing_after(
    conn: Any,
    cursor: Any,
    statement: str,
    parameters: Any,
    context: Any,
    executemany: bool,
) -> None:
    now = time.perf_counter()  # first, so nothing else is counted as database time
    if _error is not None:
        return
    try:
        timing = getattr(context, "_queryguard_timing", None)
        if timing is None:
            return
        context._queryguard_timing = None
        stmt, targets, started = timing
        stmt.duration = now - started
        for target in targets:
            target.db_time += stmt.duration
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        _fail(exc, "after_cursor_execute listener (statement timing)")


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
        if targets and conn.dialect.name == sqlite_index.DIALECT:
            sqlite_index.check(conn, statement, parameters, executemany, targets)
        elif targets and conn.dialect.name in mysql_index.DIALECTS:
            mysql_index.check(conn, cursor, statement, parameters, executemany, targets)
        elif targets:
            indexes.check(conn, statement, executemany, targets)
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        _fail(exc, "after_cursor_execute listener (missing-index check)")


def _guarded(
    handler: Callable[..., None], event_name: str | None = None
) -> Callable[..., None]:
    """A transaction or pool listener that never raises into the user's code."""

    name = event_name or getattr(handler, "__name__", "transaction")

    def listener(*args: Any) -> None:
        if _error is not None:
            return
        try:
            handler(*args)
        except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
            _fail(exc, f"{name} listener (catalog cache)")

    return listener


_on_commit = _guarded(catalog.on_commit, "commit")
_on_rollback = _guarded(catalog.on_rollback, "rollback")
_on_commit_twophase = _guarded(catalog.on_commit_twophase, "commit_twophase")
_on_rollback_twophase = _guarded(catalog.on_rollback_twophase, "rollback_twophase")
_on_rollback_savepoint = _guarded(catalog.on_rollback_savepoint, "rollback_savepoint")
_on_invalidate = _guarded(catalog.on_invalidate, "invalidate")


def _fail(exc: Exception, source: str) -> None:
    global _error, _failure
    if _error is None:
        _error = exc
        _failure = Failure(source, exc, _innermost(exc))


def record_error(exc: Exception, source: str = "plugin") -> None:
    """Disable measurement after a bug outside the listeners (first error wins)."""
    _fail(exc, source)


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
    event.listen(Session, "after_begin", _on_after_begin)
    event.listen(Engine, "before_cursor_execute", _on_cursor_execute)
    event.listen(Engine, "after_cursor_execute", _on_timing_after)
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
    event.listen(Engine, "commit_twophase", _on_commit_twophase)
    event.listen(Engine, "rollback_twophase", _on_rollback_twophase)
    event.listen(Engine, "rollback_savepoint", _on_rollback_savepoint)
    event.listen(Pool, "invalidate", _on_invalidate)
    _index_installed = True
