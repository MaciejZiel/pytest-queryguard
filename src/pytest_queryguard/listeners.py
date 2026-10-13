from __future__ import annotations

import contextlib
import itertools
import os
import re
import sys
import time
import traceback
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import ORMExecuteState, Session
from sqlalchemy.orm.session import SessionTransaction
from sqlalchemy.pool import Pool

from . import catalog, collector, indexes, mysql_index, sqlite_index, sqltext
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
# Scope numbers for the repeat rules: one per Session, one per connection that
# no Session opened. Process-wide and never reused; the number is stored on the
# Session or Connection object, so it cannot be mixed up by a recycled id().
_scopes = itertools.count(1)
_SCOPE_ATTR = "_queryguard_scope"
# The attribution of an ORM execution waits on its own Session, and a
# connection finds that Session through the weak reference that after_begin
# stores on it. Nothing here is per thread: asyncio tasks share a thread, and
# statements of other sessions or of begin listeners run between an ORM
# execution and its SELECT.
_PENDING_ATTR = "_queryguard_pending"
_SESSION_ATTR = "_queryguard_session"


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
    if _error is not None:
        return
    try:
        # Without the tags the connection counts as a scope of its own and no
        # attribution is handed to its statements.
        with contextlib.suppress(AttributeError, TypeError):
            setattr(connection, _SCOPE_ATTR, scope_of_session(session))
            # Every live Session that began on this connection: an external
            # connection can serve several (the "test in a transaction" fixture
            # with a second Session joined to it).
            refs = connection.__dict__.get(_SESSION_ATTR, ())
            if not any(ref() is session for ref in refs):
                live = tuple(ref for ref in refs if ref() is not None)
                setattr(connection, _SESSION_ATTR, (*live, weakref.ref(session)))
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        _fail(exc, "after_begin listener (session scopes)")


# Orders the ORM executions of all Sessions: of the Sessions that share a
# connection, the one that executed last owns the connection's next SELECT.
_executions = itertools.count(1)


def _set_pending(
    session: Any,
    name: str | None,
    column_load: ColumnLoadEvent | None = None,
) -> None:
    """Park the attribution of the ORM execution of ``session``.

    An execution with nothing to attribute is recorded too, so that it
    supersedes an older attribution parked by another Session of the same
    connection whose SELECT never ran.
    """
    try:
        session.__dict__[_PENDING_ATTR] = (
            next(_executions),
            collector.generation(),
            name,
            column_load,
        )
    except (AttributeError, TypeError):
        pass


def _latest(conn: Any) -> tuple[Any, Any]:
    """(Session, parked entry) of the latest ORM execution on ``conn``."""
    best: Any = None
    owner: Any = None
    for ref in conn.__dict__.get(_SESSION_ATTR, ()):
        session = ref()
        if session is None:
            continue
        entry = session.__dict__.get(_PENDING_ATTR)
        if entry is not None and (best is None or entry[0] > best[0]):
            best, owner = entry, session
    return owner, best


def _take_pending(conn: Any) -> tuple[str | None, ColumnLoadEvent | None]:
    """(relationship, column load) of the ORM execution that ``conn`` serves.

    Only the Sessions that began on the connection are asked; a statement on
    any other connection leaves every parked attribution alone.
    """
    try:
        session, entry = _latest(conn)
        if entry is None or (entry[2] is None and entry[3] is None):
            return None, None
        session.__dict__[_PENDING_ATTR] = (entry[0], entry[1], None, None)
    except (AttributeError, TypeError):
        return None, None
    if entry[1] == collector.generation():
        return entry[2], entry[3]
    return None, None


# Code of the method that opens a Session's connection: it runs the engine's
# begin event and the after_begin listeners. Statements issued while it is on
# the stack belong to those listeners, never to the ORM execution waiting for
# its SELECT. None when this SQLAlchemy has no such method (then nothing is
# skipped, as without the check).
_BEGIN_CODE = getattr(
    getattr(SessionTransaction, "_connection_for_bind", None), "__code__", None
)


def _inside_begin() -> bool:
    if _BEGIN_CODE is None:
        return False
    frame = sys._getframe(1)
    while frame is not None:
        if frame.f_code is _BEGIN_CODE:
            return True
        frame = frame.f_back  # type: ignore[assignment]
    return False


def _has_pending(conn: Any) -> bool:
    try:
        entry = _latest(conn)[1]
        return entry is not None and (entry[2] is not None or entry[3] is not None)
    except (AttributeError, TypeError):
        return False


def _reset_for_tests() -> None:
    global _error, _failure
    _error = None
    _failure = None


def _record_column_load(
    state: ORMExecuteState, targets: tuple[collector.QueryCollector, ...]
) -> None:
    """Note an instance that loads columns it already has a row for."""
    instance = getattr(state.load_options, "_refresh_state", None)
    if instance is None:
        _set_pending(state.session, None)
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
    _set_pending(state.session, None, column_load=load)
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
            _set_pending(state.session, None)
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
        _set_pending(state.session, name)
        site = find_callsite()
        load = RelationshipLoadEvent(
            name, key, site, scalar, scope_of_session(state.session)
        )
        for target in targets:
            target.add_relationship_load(load)
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        _fail(exc, "do_orm_execute listener (relationship loads)")


_is_write = sqltext.is_write
_READ_VERBS = frozenset(("SELECT", "WITH"))


def _is_read(sql: str) -> bool:
    """Whether the statement is a SELECT or a WITH (after comments and parentheses)."""
    start = sqltext.skip_prefix(sql)
    if start is None:
        return False
    word = sql[start : start + 6].upper()
    return word == "SELECT" or word.startswith("WITH")


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
        # So is a BEGIN, SAVEPOINT or SET that a begin listener runs, and so is
        # a statement on the connection of another Session.
        write = _is_write(statement)
        relationship, column_load = None, None
        if (
            not write
            and _has_pending(conn)
            and _is_read(statement)
            and not _inside_begin()
        ):
            relationship, column_load = _take_pending(conn)
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
            no_parameters = bool(
                getattr(context, "execution_options", {}).get("no_parameters")
            )
            mysql_index.check(
                conn, cursor, statement, parameters, executemany, targets, no_parameters
            )
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


def _leaks_yield_per() -> bool:
    """Whether this SQLAlchemy lets a ``do_orm_execute`` listener leak ``yield_per``.

    From 2.0.20 to 2.0.49 any listener makes the outer statement's
    ``yield_per`` reach its ``selectinload`` statement, which then raises
    "Can't use the ORM yield_per feature in conjunction with unique()"
    (SQLAlchemy issue 13301, fixed in 2.0.50).
    """
    import sqlalchemy

    found = re.match(r"(\d+)\.(\d+)\.(\d+)", sqlalchemy.__version__)
    if found is None:
        return False
    major, minor, patch = (int(part) for part in found.groups())
    return (major, minor) == (2, 0) and 20 <= patch < 50


def _on_orm_execute_heal(state: Any) -> None:
    """Drop the leaked ``yield_per`` from a statement the ORM runs for a loader.

    Registered only where ``_leaks_yield_per()`` is true. The statements the
    ORM issues for ``selectinload`` and the like carry the outer statement's
    ``yield_per`` in their execution options but have none of their own in
    the load options; without a listener they never see it.
    """
    try:
        options = state.local_execution_options
        if "yield_per" not in options or "sa_top_level_orm_context" not in options:
            return
        if getattr(options.get("_sa_orm_load_options"), "_yield_per", None):
            return
        state.local_execution_options = options.__class__(
            {key: value for key, value in options.items() if key != "yield_per"}
        )
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        _fail(exc, "do_orm_execute listener (yield_per)")


def install() -> None:
    """Register the class-level listeners once per process."""
    global _installed
    if _installed:
        return
    event.listen(Session, "do_orm_execute", _on_orm_execute)
    if _leaks_yield_per():
        event.listen(Session, "do_orm_execute", _on_orm_execute_heal)
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
