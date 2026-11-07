"""The Django ORM integration: statements, lazy loads and deferred-field loads.

It records the events the SQLAlchemy listeners record (``events.py``) into
the same collectors, so the rules, budgets, reports and files work unchanged.
``backends.py`` loads this module only once the application has imported
Django, and ``ensure()`` hooks in only once the settings are configured and
the app registry is ready. No settings change is needed: nothing goes into
``INSTALLED_APPS`` or ``MIDDLEWARE``.

**Statements** come from ``connection.execute_wrapper`` (public API since
Django 2.0): the wrapper is put first in ``execute_wrappers`` of every
connection, as connections are created (``connection_created``) and for those
of the measuring thread when a measurement starts. First, so a user's
``with connection.execute_wrapper(...)`` block, which pops the last wrapper,
never takes it off.

**Lazy loads** are seen through a few class-level patches, installed once per
process (and again for a new import of Django, as pytester sessions make):

- ``ForwardManyToOneDescriptor.get_object``: a foreign key or forward
  one-to-one that is not cached (Django 6.1 reaches it through
  ``FetchOne.fetch``; ``FETCH_PEERS`` loads all peers at once and does not);
- ``ReverseOneToOneDescriptor.__get__``: a reverse one-to-one;
- the managers of reverse foreign keys and many-to-many relations
  (``create_reverse_many_to_one_manager``, ``create_forward_many_to_many_manager``):
  their ``get_queryset()`` marks the query (``_Load`` on ``Query.__dict__``,
  which ``Query.clone()`` copies), and ``SQLCompiler.execute_sql`` reads the
  mark when such a query runs: ``author.books.all()``, ``.filter()``,
  ``.count()``; a prefetched relation answers from its cache and runs nothing;
- ``DeferredAttribute.__get__``: a field left out by ``.only()``/``.defer()``
  (a non-data descriptor: called only when the value is not loaded);
- ``ModelIterable.__iter__``: which query line loaded each instance, so lazy
  loads are grouped by the line of the parents' query, as for SQLAlchemy;
- ``Collector.delete``, ``QuerySet.bulk_create`` and ``QuerySet.bulk_update``:
  their statements form one unit of work, which the write-loop rule counts once.

What other tools got wrong, avoided here: no closure or other unpicklable
object is put on a queryset or an instance (the marks and origins pickle as a
plain ``object()``); no result cache is read or written; model instances are
never compared or hashed (custom ``__eq__``); every patch passes straight
through while nothing is measured, and never lets a plugin bug reach the
code under test (the error is recorded and measurement stops, as for the
SQLAlchemy listeners).
"""

from __future__ import annotations

import functools
import importlib
import inspect
import itertools
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from typing import Any

from . import blocks, callsite, collector, listeners, sqltext
from .callsite import find_callsite
from .events import (
    CAUSE_DEFERRED,
    ORM_DJANGO,
    ColumnLoadEvent,
    ParentLoad,
    RelationshipLoadEvent,
    StatementEvent,
)

_MARK = "_queryguard_load"
_ORIGIN = "_queryguard_origin"
_ORIGINAL = "_queryguard_original"
_INSTRUMENTED = "_queryguard_instrumented"
_LABEL = "_queryguard_label"
_DISPATCH_UID = "pytest_queryguard.django_backend"

# The django.db.models.query module the patches were installed on; a new
# import of Django (pytester purges sys.modules) is patched again.
_installed_for: object = None
_connections: Any = None  # django.db.connections of that import
_index_check = False
# The live collectors that want loads; never rebound (see collector.py).
_collectors = collector._full_stack


class _Frozen:
    """Copied as itself; pickled as a plain ``object()``.

    The marks and origins below live on objects users copy, pickle and cache
    (a ``Query``, an instance's ``_state``): they must never break that, and
    a pickle must not refer to this package.
    """

    __slots__ = ()

    def __reduce__(self) -> tuple[Any, ...]:
        return (object, ())

    def __copy__(self) -> _Frozen:
        return self

    def __deepcopy__(self, memo: Any) -> _Frozen:
        return self


class _Origin(_Frozen):
    """Where an instance was loaded: the query line, its class and the path."""

    __slots__ = ("noted", "path", "root", "site")

    def __init__(
        self,
        site: Any,
        root: str | None,
        path: tuple[tuple[str, bool], ...],
        noted: int,
    ) -> None:
        self.site = site
        self.root = root
        self.path = path
        self.noted = noted


class _Load(_Frozen):
    """A collection load: the mark a related manager puts on its query."""

    __slots__ = ("key", "label", "origin")

    def __init__(self, label: str, key: object, origin: _Origin | None) -> None:
        self.label = label
        self.key = key
        self.origin = origin


# The load whose SELECT is about to run in this context: (its event, the
# origin of the instances it loads). The first SELECT takes it.
_Pending = tuple[RelationshipLoadEvent | ColumnLoadEvent, _Origin | None]
_pending: ContextVar[_Pending | None] = ContextVar(
    "queryguard_django_load", default=None
)
# Set inside a related manager's write method (add, set, create, ...): the
# SELECTs they run are bookkeeping, not a read of the relation.
_writing: ContextVar[bool] = ContextVar("queryguard_django_writing", default=False)
# The unit of work (a delete with its cascades, a bulk create or update) the
# statements of this context belong to; negative, so never equal to a
# SQLAlchemy flush number.
_unit: ContextVar[int | None] = ContextVar("queryguard_django_unit", default=None)
_units = itertools.count(1)


class _Rows(threading.local):
    """The latest SELECT of this thread, for the instances its rows become."""

    seq = 0
    origin: _Origin | None = None
    inherited = False  # a lazy load's: its rows are reached through the parent


_rows = _Rows()


def _failed() -> bool:
    return listeners.internal_error() is not None


def _fail(exc: Exception, source: str) -> None:
    listeners.record_error(exc, source)


# --- installing -----------------------------------------------------------


def ensure(*, index_check: bool = False) -> None:
    """Hook Django in once it is ready, and wrap this thread's connections.

    Called when a measurement starts (and when the plugin installs). Cheap
    when there is nothing to do; never raises.
    """
    global _index_check
    if index_check:
        _index_check = True
    try:
        if not _ready():
            return
        query = sys.modules.get("django.db.models.query")
        if query is None or query is not _installed_for:
            _install()
        _attach_all()
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        _fail(exc, "Django integration (install)")


def _ready() -> bool:
    conf = sys.modules.get("django.conf")
    apps = sys.modules.get("django.apps")
    if conf is None or apps is None:
        return False
    if not conf.settings.configured:
        return False
    return bool(apps.apps.ready)


def _module(name: str) -> Any:
    return importlib.import_module(name)


def _patch(owner: Any, name: str, make: Callable[[Any], Any]) -> None:
    """Replace ``owner.name`` with ``make(original)``, once."""
    original = owner.__dict__[name]
    if getattr(original, _ORIGINAL, None) is not None:
        return
    wrapper = make(original)
    setattr(wrapper, _ORIGINAL, original)
    setattr(owner, name, wrapper)


def _install() -> None:
    global _installed_for, _connections
    query = _module("django.db.models.query")
    descriptors = _module("django.db.models.fields.related_descriptors")
    query_utils = _module("django.db.models.query_utils")
    compiler = _module("django.db.models.sql.compiler")
    deletion = _module("django.db.models.deletion")
    signals = _module("django.db.backends.signals")

    _patch(descriptors.ForwardManyToOneDescriptor, "get_object", _forward_get_object)
    _patch(descriptors.ReverseOneToOneDescriptor, "__get__", _reverse_one_get)
    _patch(query_utils.DeferredAttribute, "__get__", _deferred_get)
    _patch(query.ModelIterable, "__iter__", _model_iter)
    writes = (
        compiler.SQLInsertCompiler,
        compiler.SQLUpdateCompiler,
        compiler.SQLDeleteCompiler,
    )
    _patch(compiler.SQLCompiler, "execute_sql", lambda o: _execute_sql(o, writes))
    _patch(deletion.Collector, "delete", _in_unit)
    _patch(query.QuerySet, "bulk_create", _in_unit)
    _patch(query.QuerySet, "bulk_update", _in_unit)
    _patch_factories(descriptors)
    signals.connection_created.connect(
        _on_connection_created, weak=False, dispatch_uid=_DISPATCH_UID
    )
    # Frames of Django, asgiref and DRF are never the application's, also
    # when installed outside site-packages; classify the files again.
    callsite.reset_internal()
    _connections = _module("django.db").connections
    _installed_for = query


def _patch_factories(descriptors: Any) -> None:
    """Instrument every related manager class, also those made already."""
    factories = [
        (descriptors, "create_reverse_many_to_one_manager"),
        (descriptors, "create_forward_many_to_many_manager"),
    ]
    generic = sys.modules.get("django.contrib.contenttypes.fields")
    if generic is not None:
        factories.append((generic, "create_generic_related_manager"))
    for module, name in factories:
        original = getattr(module, name, None)
        if original is None:
            continue
        if getattr(original, _ORIGINAL, None) is not None:
            continue

        def factory(*args: Any, __original: Any = original, **kwargs: Any) -> Any:
            cls = __original(*args, **kwargs)
            _instrument_manager(cls)
            return cls

        setattr(factory, _ORIGINAL, original)
        setattr(module, name, factory)
    # Descriptors cache their manager class (related_manager_cls); those
    # built before the patch are instrumented here.
    apps = _module("django.apps").apps
    kinds = (descriptors.ReverseManyToOneDescriptor, descriptors.ManyToManyDescriptor)
    for model in apps.get_models(include_auto_created=True):
        for value in list(vars(model).values()):
            if isinstance(value, kinds):
                cls = vars(value).get("related_manager_cls")
                if cls is not None:
                    _instrument_manager(cls)


def _on_connection_created(sender: Any, connection: Any, **kwargs: Any) -> None:
    try:
        _attach(connection)
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        _fail(exc, "Django integration (connection_created)")


def _attach_all() -> None:
    """Wrap the connections this thread has made so far."""
    connections = _module("django.db").connections
    for connection in connections.all(initialized_only=True):
        _attach(connection)


def _attach(connection: Any) -> None:
    wrappers = connection.execute_wrappers
    for wrapper in wrappers:
        if wrapper is _execute:
            return
    wrappers.insert(0, _execute)


# --- statements -----------------------------------------------------------


def _execute(
    execute: Callable[..., Any], sql: Any, params: Any, many: bool, context: Any
) -> Any:
    """The execute wrapper: record the statement, then run it and time it."""
    if _failed():
        return execute(sql, params, many, context)
    targets = collector.targets()
    if not targets:
        return execute(sql, params, many, context)
    try:
        stmt = _record(sql, params, many, context, targets)
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        _fail(exc, "Django execute wrapper (statements)")
        return execute(sql, params, many, context)
    started = time.perf_counter()
    result = execute(sql, params, many, context)
    duration = time.perf_counter() - started
    try:
        stmt.duration = duration
        for target in targets:
            target.add_db_time(duration)
        if _index_check:
            _check_index(sql, params, many, context)
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        _fail(exc, "Django execute wrapper (after the statement)")
    return result


def _record(
    sql: Any,
    params: Any,
    many: bool,
    context: Any,
    targets: tuple[collector.QueryCollector, ...],
) -> StatementEvent:
    sql = str(sql)
    write = sqltext.is_write(sql)
    ignores = blocks.current()
    full = collector.full_active()
    pending = None
    if not write:
        pending = _pending.get()
        if pending is not None:
            if sqltext.is_transaction_control(sql):
                pending = None
            else:
                _pending.set(None)  # the load's own SELECT; later ones are not
    relationship = None
    column_load = False
    site = None
    if pending is not None and full:
        event = pending[0]
        site = event.callsite
        if isinstance(event, RelationshipLoadEvent):
            relationship = event.relationship
            for target in full:
                target.add_relationship_load(listeners._for_target(event, target))
        else:
            column_load = True
            event.sql = sql
            for target in full:
                target.add_column_load(event)
    if site is None:
        site = find_callsite()
    stmt = StatementEvent(
        sql,
        site,
        relationship,
        column_load=column_load,
        lazy_load=relationship is not None,
        parameters=None if many else params,
        executemany=bool(many),
        flush=_unit.get() if write else None,
        scope=listeners.scope_of_connection(context["connection"]),
        ignores=ignores,
        orm=ORM_DJANGO,
    )
    for target in targets:
        target.add_statement(stmt)
    if full and not write:
        rows = _rows
        rows.seq += 1
        if pending is not None and isinstance(pending[0], RelationshipLoadEvent):
            rows.origin, rows.inherited = pending[1], True
        elif pending is not None:
            rows.origin, rows.inherited = None, True  # a refresh: no new parents
        else:
            rows.origin = _Origin(site, None, (), collector._generation)
            rows.inherited = False
    return stmt


class _Connection:
    """What the index checks read from an SQLAlchemy ``Connection``.

    ``engine`` is the Django connection (per alias and thread): the explained
    statements and the catalog are cached per engine. ``dialect`` is read by
    the PostgreSQL check only.
    """

    __slots__ = ("connection", "dialect", "engine")

    def __init__(self, wrapper: Any, dialect: Any = None) -> None:
        self.engine = wrapper
        self.connection = _DbapiHolder(wrapper.connection)
        self.dialect = dialect


class _DbapiHolder:
    __slots__ = ("dbapi_connection",)

    def __init__(self, dbapi_connection: Any) -> None:
        self.dbapi_connection = dbapi_connection


class _PostgresDialect:
    """The few dialect attributes the PostgreSQL check reads."""

    __slots__ = ("driver", "server_version_info")
    name = "postgresql"
    paramstyle = "pyformat"
    is_async = False

    def __init__(self, wrapper: Any) -> None:
        database = getattr(wrapper, "Database", None)
        self.driver = getattr(database, "__name__", "psycopg").split(".")[0]
        self.server_version_info = tuple(wrapper.get_database_version())


def _raw_cursor(cursor: Any) -> Any:
    """The DB-API cursor under Django's cursor wrappers."""
    for _ in range(3):
        inner = getattr(cursor, "cursor", None)
        if inner is None:
            break
        cursor = inner
    return cursor


def _check_index(sql: str, params: Any, many: bool, context: Any) -> None:
    """The missing-index check of one statement that ran."""
    from . import catalog, indexes, mysql_index, sqlite_index

    if catalog.changes_catalog(sql):
        catalog.clear()  # DDL: the cached catalog and explains are stale
    targets = [target for target in collector.active() if target.index_check]
    if not targets:
        return
    wrapper = context["connection"]
    vendor = wrapper.vendor
    if vendor == "sqlite":
        statement = sql
        if params is not None:
            names = list(params) if isinstance(params, dict) else None
            statement = _raw_cursor(context["cursor"]).convert_query(
                sql, param_names=names
            )
        sqlite_index.check(_Connection(wrapper), statement, params, many, targets)
    elif vendor == "mysql":
        cursor = _raw_cursor(context["cursor"])
        mysql_index.check(_Connection(wrapper), cursor, sql, params, many, targets)
    elif vendor == "postgresql":
        dialect = _PostgresDialect(wrapper)
        indexes.check(_Connection(wrapper, dialect), sql, many, targets)
    else:
        notice = (
            f"{vendor} statements were not checked: the index check supports "
            f"{indexes.SUPPORTED_DATABASES}"
        )
        for target in targets:
            target.index_notices[notice] = None


# --- where instances come from -----------------------------------------------


def _model_iter(original: Callable[[Any], Iterator[Any]]) -> Callable[[Any], Any]:
    def __iter__(self: Any) -> Iterator[Any]:
        rows = original(self)
        if not _collectors or _failed():
            return rows
        return _noting(rows, self.queryset)

    return __iter__


def _noting(rows: Iterator[Any], queryset: Any) -> Iterator[Any]:
    """Yield the instances of ``rows``, noting the query line of each."""
    local = _rows
    before = local.seq
    origin: _Origin | None = None
    first = True
    for obj in rows:
        if first:
            first = False
            try:
                origin = _rows_origin(local, before, queryset)
            except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
                _fail(exc, "Django integration (instance origins)")
        if origin is not None:
            obj._state.__dict__[_ORIGIN] = origin
        yield obj


def _rows_origin(local: _Rows, before: int, queryset: Any) -> _Origin | None:
    if local.seq == before:
        return None  # its SELECT was not seen (another connection, a cache)
    origin = local.origin
    if origin is None or local.inherited:
        return origin
    return _Origin(origin.site, queryset.model.__name__, (), origin.noted)


def _origin_of(instance: Any) -> _Origin | None:
    origin = instance._state.__dict__.get(_ORIGIN)
    return origin if isinstance(origin, _Origin) else None


def _parent(origin: _Origin | None) -> ParentLoad | None:
    """The parent's origin, if noted while the outermost measurement ran."""
    if origin is None:
        return None
    full = collector._active_full
    if not full or origin.noted < full[0].since:
        return None
    if origin.site is None and origin.root is None:
        return None
    return ParentLoad(origin.site, origin.root, origin.path, origin.noted)


def _child_origin(origin: _Origin | None, label: str, scalar: bool) -> _Origin | None:
    """The origin of the instances a lazy load of ``origin``'s parent loads."""
    if origin is None:
        return None
    path = origin.path + ((label, scalar),) if origin.root is not None else ()
    return _Origin(origin.site, origin.root, path, origin.noted)


def _key(instance: Any) -> object:
    return (instance._meta.label, instance.pk)


def _scope(instance: Any) -> int | None:
    """The scope of the connection a lazy load of ``instance`` uses (mostly)."""
    connections = _connections
    if connections is None:
        return None
    return listeners.scope_of_connection(connections[instance._state.db or "default"])


# --- lazy loads -------------------------------------------------------------


def _relationship_event(
    label: str, instance: Any, scalar: bool, scope: int | None, origin: _Origin | None
) -> RelationshipLoadEvent:
    return RelationshipLoadEvent(
        label,
        _key(instance),
        find_callsite(),
        scalar,
        scope,
        _parent(origin),
        blocks.current(),
        ORM_DJANGO,
    )


def _begin(event: RelationshipLoadEvent) -> None:
    """Strict mode: raise at the lazy load that makes the N+1, before its SELECT."""
    full = collector.full_active()
    strict: BaseException | None = None
    raiser: collector.QueryCollector | None = None
    for target in full:
        if target.strict is not None:
            error = target.strict_check(listeners._for_target(event, target))
            if strict is None and error is not None:
                strict, raiser = error, target
    if strict is None:
        return
    # a load that raises runs no SELECT: it is recorded here
    for target in full:
        target.add_relationship_load(listeners._for_target(event, target))
    if raiser is not None:
        raiser.strict_raised = True
    # raised inside Django's ``except KeyError`` of an uncached relation: that
    # KeyError is no part of the report
    strict.__suppress_context__ = True
    raise strict


def _prepare(
    label: str, instance: Any, scalar: bool, scope: int | None
) -> _Pending | None:
    """The pending load of a relation of ``instance``; None after a plugin error."""
    try:
        origin = _origin_of(instance)
        event = _relationship_event(label, instance, scalar, scope, origin)
        return event, _child_origin(origin, label, scalar)
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        _fail(exc, "Django integration (lazy load)")
        return None


def _run_load(pending: _Pending | None, call: Callable[[], Any]) -> Any:
    if pending is None:
        return call()
    event = pending[0]
    if isinstance(event, RelationshipLoadEvent):
        _begin(event)  # raises in strict mode: outside any try, on purpose
    token = _pending.set(pending)
    try:
        return call()
    finally:
        _pending.reset(token)


def _forward_get_object(original: Callable[[Any, Any], Any]) -> Callable[..., Any]:
    def get_object(self: Any, instance: Any) -> Any:
        if not _collectors or _failed():
            return original(self, instance)
        field = self.field
        pending = _prepare(
            f"{field.model.__name__}.{field.name}", instance, True, _scope(instance)
        )
        return _run_load(pending, lambda: original(self, instance))

    return get_object


def _reverse_one_get(original: Callable[..., Any]) -> Callable[..., Any]:
    def __get__(self: Any, instance: Any, cls: Any = None) -> Any:
        if (
            instance is None
            or not _collectors
            or _failed()
            or self.related.is_cached(instance)
            or instance.pk is None
        ):
            return original(self, instance, cls)
        related = self.related
        pending = _prepare(
            f"{related.model.__name__}.{related.get_accessor_name()}",
            instance,
            True,
            _scope(instance),
        )
        return _run_load(pending, lambda: original(self, instance, cls))

    return __get__


def _deferred_get(original: Callable[..., Any]) -> Callable[..., Any]:
    def __get__(self: Any, instance: Any, cls: Any = None) -> Any:
        if (
            instance is None
            or not _collectors
            or self.field.attname in instance.__dict__
            or getattr(self.field, "generated", False)
            or _failed()
        ):
            # a GeneratedField is reloaded by Django itself after save()
            return original(self, instance, cls)
        try:
            event = ColumnLoadEvent(
                type(instance).__name__,
                (self.field.name,),
                CAUSE_DEFERRED,
                _key(instance),
                find_callsite(),
                scope=_scope(instance),
                ignores=blocks.current(),
                orm=ORM_DJANGO,
            )
            pending: _Pending | None = (event, None)
        except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
            _fail(exc, "Django integration (deferred field)")
            pending = None
        return _run_load(pending, lambda: original(self, instance, cls))

    return __get__


# --- related managers (reverse foreign keys, many-to-many) ------------------

# Their methods that write; the SELECTs inside are not a read of the relation.
# The async ones (aadd, aset, ...) run these in a thread (sync_to_async).
_MANAGER_WRITES = (
    "add",
    "remove",
    "clear",
    "set",
    "create",
    "get_or_create",
    "update_or_create",
)


def _instrument_manager(cls: Any) -> None:
    if cls.__dict__.get(_INSTRUMENTED):
        return
    original = cls.__dict__.get("get_queryset")
    if original is None:
        return

    def get_queryset(self: Any) -> Any:
        queryset = original(self)
        if _collectors and not _writing.get() and not _failed():
            try:
                _mark(queryset, self)
            except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
                _fail(exc, "Django integration (related manager)")
        return queryset

    cls.get_queryset = get_queryset
    # many-to-many count() and exists() query the through table alone
    target = cls.__dict__.get("constrained_target")
    if isinstance(target, property) and target.fget is not None:
        cls.constrained_target = property(_constrained(target.fget))
    for name in _MANAGER_WRITES:
        method = cls.__dict__.get(name)
        if method is not None and not inspect.iscoroutinefunction(method):
            setattr(cls, name, _writing_method(method))
    setattr(cls, _INSTRUMENTED, True)


def _constrained(original: Callable[[Any], Any]) -> Callable[[Any], Any]:
    def constrained_target(self: Any) -> Any:
        queryset = original(self)
        if (
            queryset is not None
            and _collectors
            and not _writing.get()
            and not _failed()
        ):
            try:
                _mark(queryset, self)
            except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
                _fail(exc, "Django integration (related manager)")
        return queryset

    return constrained_target


def _writing_method(original: Callable[..., Any]) -> Callable[..., Any]:
    # functools.wraps keeps alters_data and queryset_only (the function's
    # __dict__) visible to templates and managers
    @functools.wraps(original)
    def method(self: Any, *args: Any, **kwargs: Any) -> Any:
        token = _writing.set(True)
        try:
            return original(self, *args, **kwargs)
        finally:
            _writing.reset(token)

    return method


def _manager_label(manager: Any) -> str:
    """``Author.books`` for the related manager of ``author.books``."""
    cls = type(manager)
    label = cls.__dict__.get(_LABEL)
    if label is not None:
        return str(label)
    # instance attributes: Manager proxies QuerySet methods such as reverse()
    attributes = vars(manager)
    if "content_type_field_name" in attributes:  # a GenericRelation
        field = manager.instance._meta.get_field(manager.prefetch_cache_name)
        label = f"{field.model.__name__}.{field.name}"
    elif "query_field_name" in attributes:  # many-to-many, either side
        if attributes["reverse"]:
            field = manager.model._meta.get_field(manager.query_field_name)
            rel = field.remote_field
            label = f"{rel.model.__name__}.{rel.get_accessor_name()}"
        else:
            field = manager.instance._meta.get_field(manager.prefetch_cache_name)
            label = f"{field.model.__name__}.{field.name}"
    else:  # the reverse side of a foreign key
        rel = manager.field.remote_field
        label = f"{rel.model.__name__}.{rel.get_accessor_name()}"
    setattr(cls, _LABEL, label)
    return label


def _mark(queryset: Any, manager: Any) -> None:
    instance = manager.instance
    # _query, not query: the property would apply a deferred filter now
    query = queryset._query
    query.__dict__[_MARK] = _Load(
        _manager_label(manager), _key(instance), _origin_of(instance)
    )


def _execute_sql(
    original: Callable[..., Any], writes: tuple[type, ...]
) -> Callable[..., Any]:
    def execute_sql(self: Any, *args: Any, **kwargs: Any) -> Any:
        mark = self.query.__dict__.get(_MARK)
        if (
            mark is None
            or not _collectors
            or not isinstance(mark, _Load)
            or isinstance(self, writes)
            or _failed()
        ):
            return original(self, *args, **kwargs)
        try:
            event = RelationshipLoadEvent(
                mark.label,
                mark.key,
                find_callsite(),
                False,
                listeners.scope_of_connection(self.connection),
                _parent(mark.origin),
                blocks.current(),
                ORM_DJANGO,
            )
            pending: _Pending | None = (
                event,
                _child_origin(mark.origin, mark.label, False),
            )
        except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
            _fail(exc, "Django integration (related manager query)")
            pending = None
        return _run_load(pending, lambda: original(self, *args, **kwargs))

    return execute_sql


# --- units of work for the write-loop rule ----------------------------------


def _in_unit(original: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(original)
    def method(self: Any, *args: Any, **kwargs: Any) -> Any:
        if _unit.get() is not None or not collector._active:
            return original(self, *args, **kwargs)
        token = _unit.set(-next(_units))
        try:
            return original(self, *args, **kwargs)
        finally:
            _unit.reset(token)

    return method
