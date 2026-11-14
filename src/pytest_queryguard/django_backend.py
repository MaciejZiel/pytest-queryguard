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
  their statements form one unit of work, which the write-loop rule counts once;
- ``send`` and ``send_robust`` of the model signals (``post_save``, ...): the
  receivers of a signal that a related manager's write or a ``delete()`` sends
  read and write as usual.

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
import itertools
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextvars import ContextVar
from typing import Any

from . import blocks, callsite, collector, errors, sqltext
from .callsite import NO_ORIGIN, Origin, find_callsite, find_site_origin
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
# The django.test.testcases module whose set-up and tear-down are patched
_testcases_for: object = None
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

    __slots__ = ("deferring", "noted", "path", "root", "site")

    def __init__(
        self,
        site: Any,
        root: str | None,
        path: tuple[tuple[str, bool], ...],
        noted: int,
        deferring: bool = False,
    ) -> None:
        self.site = site
        self.root = root
        self.path = path
        self.noted = noted
        # the query left out fields (.only(), .defer())
        self.deferring = deferring


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
# Inside the database set-up and tear-down of Django's own test cases
# (TransactionTestCase._pre_setup and _post_teardown: fixtures loaded, the
# database flushed, post_migrate emitted): unittest runs them inside the test
# call, but they are not the test's code, and assertNumQueries does not count
# them either. Nothing is recorded there.
_machinery: ContextVar[bool] = ContextVar("queryguard_django_machinery", default=False)
_units = itertools.count(1)
# The application line that awaited the async ORM call (aget(), async for)
# running in this thread through asgiref's sync_to_async: the thread itself
# has no frame of the application. asgiref copies the context into it.
_async_site: ContextVar[Any] = ContextVar("queryguard_django_async_site", default=None)


class _Rows(threading.local):
    """The latest SELECT of this thread, for the instances its rows become."""

    seq = 0
    origin: _Origin | None = None
    inherited = False  # a lazy load's: its rows are reached through the parent


_rows = _Rows()


def _failed() -> bool:
    return errors._error is not None


def _fail(exc: Exception, source: str) -> None:
    errors.record_error(exc, source)


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
        if _testcases_for is not sys.modules.get("django.test.testcases"):
            _patch_testcases()
        _attach_all()
        # a streaming response never read to its end sends no request_finished
        _request.set(None)
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
    # The statements Django runs to set up a new connection (MySQL's SET of
    # the isolation level and SQL_AUTO_IS_NULL) are not the code's: not
    # counted, as assertNumQueries connects before it counts
    base = _module("django.db.backends.base.base")
    _patch(base.BaseDatabaseWrapper, "connect", _outside_test)
    _patch(query.QuerySet, "bulk_create", _in_unit)
    _patch(query.QuerySet, "bulk_update", _in_unit)
    # delete() reads the rows of a cascade with the queryset it was called on,
    # which may be a related manager's: that SELECT is no read of the relation
    _patch(query.QuerySet, "delete", _writing_method)
    # ... but the receivers of the signals such a write sends (post_save of
    # author.books.create(), post_delete of a queryset's delete()) are the
    # application's code: their reads are reads
    # (only these signals: Model.__init__ sends pre_init and post_init for
    # every instance, which a wrapper on Signal.send would slow down)
    model_signals = _module("django.db.models.signals")
    for name in ("pre_save", "post_save", "pre_delete", "post_delete", "m2m_changed"):
        signal = getattr(model_signals, name)
        for method in ("send", "send_robust"):
            bound = getattr(signal, method)
            if getattr(bound, _ORIGINAL, None) is None:
                wrapper = _receivers(bound)
                setattr(wrapper, _ORIGINAL, bound)
                setattr(signal, method, wrapper)
    _patch_factories(descriptors)
    generic = sys.modules.get("django.contrib.contenttypes.fields")
    if generic is not None:
        # Django 6.1 moved __get__ to a descriptor class of its own
        owner = getattr(generic, "GenericForeignKeyDescriptor", None)
        if owner is not None:
            _patch(owner, "__get__", lambda o: _generic_get(o, lambda d: d.field))
        else:
            _patch(
                generic.GenericForeignKey, "__get__", lambda o: _generic_get(o, None)
            )
    signals.connection_created.connect(
        _on_connection_created, weak=False, dispatch_uid=_DISPATCH_UID
    )
    requests = _module("django.core.signals")
    requests.request_started.connect(
        _on_request_started, weak=False, dispatch_uid=_DISPATCH_UID
    )
    requests.request_finished.connect(
        _on_request_finished, weak=False, dispatch_uid=_DISPATCH_UID
    )
    sync = sys.modules.get("asgiref.sync")
    if sync is not None and hasattr(sync, "SyncToAsync"):
        _patch(sync.SyncToAsync, "__call__", _sync_to_async_call)
    # Frames of Django, asgiref and DRF are never the application's, also
    # when installed outside site-packages; classify the files again.
    callsite.reset_internal()
    _connections = _module("django.db").connections
    _installed_for = query


def _patch_testcases() -> None:
    """Record nothing inside the set-up and tear-down of Django's test cases."""
    global _testcases_for
    testcases = sys.modules.get("django.test.testcases")
    if testcases is None:
        return
    owner = testcases.TransactionTestCase
    for name in ("_pre_setup", "_post_teardown"):
        original = owner.__dict__.get(name)
        if original is None:
            continue
        if isinstance(original, classmethod):
            function = original.__func__
            if getattr(function, _ORIGINAL, None) is None:
                setattr(owner, name, classmethod(_outside_test(function)))
        elif getattr(original, _ORIGINAL, None) is None:
            setattr(owner, name, _outside_test(original))
    _testcases_for = testcases


def _outside_test(original: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(original)
    def method(*args: Any, **kwargs: Any) -> Any:
        token = _machinery.set(True)
        try:
            return original(*args, **kwargs)
        finally:
            _machinery.reset(token)

    setattr(method, _ORIGINAL, original)
    return method


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
    connections = _connections
    if connections is None:
        connections = _module("django.db").connections
    for connection in connections.all(initialized_only=True):
        _attach(connection)


def attach() -> None:
    """Wrap this thread's connections, when the integration is installed.

    For a context-local collector (``record_queries()``, the fixture), which
    may open outside the test's measurement. Never raises.
    """
    if not _usable():
        return
    try:
        _attach_all()
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        _fail(exc, "Django integration (attach)")


def detach() -> None:
    """Unwrap this thread's connections once nothing measures.

    With an execute wrapper in its list, Django runs every statement through
    a slower path; the statements between tests (fixtures, setup and
    teardown) then pay for a wrapper that records nothing. The next
    measurement wraps them again (``ensure``), and a connection made in
    another thread keeps its wrapper (it was wrapped as it was created).
    Never raises.
    """
    if not _usable():
        return
    try:
        for connection in _connections.all(initialized_only=True):
            wrappers = connection.execute_wrappers
            for i in range(len(wrappers) - 1, -1, -1):
                if wrappers[i] is _execute:
                    del wrappers[i]
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        _fail(exc, "Django integration (detach)")


def _usable() -> bool:
    """Whether the Django the patches were installed on is still the one
    loaded and ready: attach() and detach() run between measurements, after
    a test may have unloaded Django or its settings; they then do nothing."""
    if _installed_for is None or _connections is None:
        return False
    if sys.modules.get("django.db.models.query") is not _installed_for:
        return False
    try:
        return _ready()
    except Exception:  # noqa: BLE001  # settings that fail to load: not ready
        return False


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
    if not targets or _machinery.get():
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
            _check_index(stmt.sql, params, many, context)
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
            # a lookup per item is a plain statement: the repeat rules count it
            if not _lookup_per_item(event, sql, params, full[0]):
                relationship = event.relationship
                for target in full:
                    target.add_relationship_load(collector.for_target(event, target))
        else:
            column_load = True
            event.sql = sql
            for target in full:
                target.add_column_load(event)
    # the call path, for the count rules of the collectors that want loads
    origin = NO_ORIGIN
    if full:
        found, origin = _site_origin()
        if site is None:
            site = found
    elif site is None:
        site = _site()
    stmt = StatementEvent(
        sql,
        site,
        relationship,
        column_load=column_load,
        lazy_load=relationship is not None,
        parameters=None if many else params,
        executemany=bool(many),
        flush=_unit.get() if write else None,
        scope=_scope_of(context["connection"]),
        ignores=ignores,
        path=origin.path,
        test_code=origin.test_code,
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


# (relation, parent, statement) -> the parameters it first ran with, of the
# related-manager reads of the running measurement, see _lookup_per_item.
_queried: dict[tuple[object, ...], Any] = {}
_queried_since = 0  # the ``since`` of its outermost collector


def _lookup_per_item(
    event: RelationshipLoadEvent,
    sql: str,
    params: Any,
    outermost: collector.QueryCollector,
) -> bool:
    """Whether a related manager runs, for a parent, a statement it ran for
    that parent before with other values: a lookup per item.

    Unlike a foreign key, a related manager caches nothing: one author's
    ``author.books.get(pk=pk)`` or ``.filter(title=title).exists()`` per
    item runs one statement per item for a single parent. The
    relationship-load rule counts parents, so it would never report them; the
    first is a load of the relation, the others are statements the repeat
    rules count, as ``Book.objects.get(author=author, pk=pk)`` per item. The
    same statement with the same values (``book.author.books.count()`` for
    books of one author) stays a load of the relation.
    """
    if event.scalar:
        return False
    global _queried_since
    if _queried_since != outermost.since:
        _queried.clear()
        _queried_since = outermost.since
    # by the reading line and request too: another line (a second
    # SerializerMethodField) reading the relation with another filter is a
    # read of the relation of its own
    key = (event.relationship, event.parent_key, sql, event.callsite, event.scope)
    if key in _queried:
        try:
            return bool(_queried[key] != params)
        except Exception:  # noqa: BLE001  # values that compare oddly (arrays): a load
            return False
    if len(_queried) >= 4096:
        _queried.clear()
    _queried[key] = params
    return False


class _Connection:
    """What the index checks read from an SQLAlchemy ``Connection``.

    ``engine`` is the Django connection (per alias and thread): the explained
    statements and the catalog are cached per engine. ``dialect`` is read by
    the PostgreSQL check only. One per Django connection, kept on it.
    """

    __slots__ = ("connection", "dialect", "engine")

    def __init__(self, wrapper: Any) -> None:
        self.engine = wrapper
        self.connection = _DbapiHolder(wrapper)
        self.dialect = (
            _PostgresDialect(wrapper) if wrapper.vendor == "postgresql" else None
        )


class _DbapiHolder:
    """``connection.dbapi_connection``: the Django connection's current one."""

    __slots__ = ("_wrapper",)

    def __init__(self, wrapper: Any) -> None:
        self._wrapper = wrapper

    @property
    def dbapi_connection(self) -> Any:
        return self._wrapper.connection


class _PostgresDialect:
    """The few dialect attributes the PostgreSQL check reads."""

    __slots__ = ("_version", "_wrapper", "driver")
    name = "postgresql"
    paramstyle = "pyformat"
    is_async = False

    def __init__(self, wrapper: Any) -> None:
        database = getattr(wrapper, "Database", None)
        self.driver = getattr(database, "__name__", "psycopg").split(".")[0]
        self._wrapper = wrapper
        self._version: tuple[int, ...] | None = None

    @property
    def server_version_info(self) -> tuple[int, ...]:
        if self._version is None:
            self._version = tuple(self._wrapper.get_database_version())
        return self._version


_ADAPTER = "_queryguard_index_connection"


def _adapter(wrapper: Any) -> _Connection:
    adapter = wrapper.__dict__.get(_ADAPTER)
    if adapter is None:
        adapter = _Connection(wrapper)
        setattr(wrapper, _ADAPTER, adapter)
    return adapter  # type: ignore[no-any-return]


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
    from . import catalog, indexes, sqlite_index

    if catalog.changes_catalog(sql):
        catalog.clear()  # DDL: the cached catalog and explains are stale
    targets = [target for target in collector.active() if target.index_check]
    if not targets or many:
        return
    wrapper = context["connection"]
    vendor = wrapper.vendor
    if vendor == "sqlite":
        if not sqlite_index.is_checked_statement(sql):
            return
        statement = sql
        if params is not None:  # as Django's SQLite cursor turns %s into ?
            names = list(params) if isinstance(params, Mapping) else None
            statement = _raw_cursor(context["cursor"]).convert_query(
                sql, param_names=names
            )
        sqlite_index.check(_adapter(wrapper), statement, params, many, targets)
    elif vendor == "mysql":
        # imported here: it imports SQLAlchemy (_compat), which a Django
        # project need not have imported
        from . import mysql_index

        cursor = _raw_cursor(context["cursor"])
        mysql_index.check(_adapter(wrapper), cursor, sql, params, many, targets)
    elif vendor == "postgresql":
        indexes.check(_adapter(wrapper), sql, many, targets)
    else:
        notice = indexes.unsupported_reason(_Vendor(vendor))
        for target in targets:
            if notice is not None:
                target.index_notices[notice] = None


class _Vendor:
    """A dialect stand-in that only has a name, for the unsupported notice."""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name


# --- where instances come from -----------------------------------------------


def _model_iter(original: Callable[[Any], Iterator[Any]]) -> Callable[[Any], Any]:
    def __iter__(self: Any) -> Iterator[Any]:
        rows = original(self)
        if not _collectors or _failed():
            return rows
        return _noting(rows, self.queryset)

    return __iter__


def _noting(rows: Iterator[Any], queryset: Any) -> Iterator[Any]:
    """Yield the instances of ``rows``, noting the query line of each.

    The objects ``select_related()`` built with each row are noted too, with
    the relations from the queried class to them, for the fix of a lazy
    load further down (``select_related("author__publisher")``).
    """
    local = _rows
    before = local.seq
    origin: _Origin | None = None
    related: list[tuple[tuple[str, ...], _Origin]] = []
    first = True
    try:
        for obj in rows:
            if first:
                first = False
                try:
                    origin = _rows_origin(local, before, queryset)
                except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
                    _fail(exc, "Django integration (instance origins)")
                if origin is not None and origin.root is not None:
                    try:
                        related = _related_origins(queryset, origin)
                    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
                        _fail(exc, "Django integration (select_related origins)")
            if origin is not None:
                obj._state.__dict__[_ORIGIN] = origin
                for caches, step_origin in related:
                    target = obj
                    for cache in caches:
                        target = target._state.fields_cache.get(cache)
                        if target is None:
                            break
                    else:
                        target._state.__dict__[_ORIGIN] = step_origin
            yield obj
    finally:
        # closed early (a break out of .iterator()): close Django's generator
        # too, whose finally closes its (server-side) cursor
        close = getattr(rows, "close", None)
        if close is not None:
            close()


def _related_origins(
    queryset: Any, origin: _Origin
) -> list[tuple[tuple[str, ...], _Origin]]:
    """(cache names from the row's object, origin) of each select_related step."""
    found: list[tuple[tuple[str, ...], _Origin]] = []
    tree = queryset.query.select_related
    if not isinstance(tree, dict):
        return found  # False, or True for every foreign key: not followed
    walk: list[
        tuple[Any, dict[str, Any], tuple[str, ...], tuple[tuple[str, bool], ...]]
    ]
    walk = [(queryset.model, tree, (), ())]
    while walk:
        model, branch, caches, path = walk.pop()
        for name, below in branch.items():
            field = model._meta.get_field(name)
            if hasattr(field, "get_accessor_name"):  # a reverse relation
                label = f"{model.__name__}.{field.get_accessor_name()}"
            else:
                label = f"{model.__name__}.{field.name}"
            step_caches = (*caches, field.cache_name)
            step_path = (*path, (label, True))
            found.append(
                (
                    step_caches,
                    _Origin(origin.site, origin.root, step_path, origin.noted),
                )
            )
            if isinstance(below, dict) and below:
                walk.append((field.related_model, below, step_caches, step_path))
    return found


def _rows_origin(local: _Rows, before: int, queryset: Any) -> _Origin | None:
    if local.seq == before:
        return None  # its SELECT was not seen (another connection, a cache)
    origin = local.origin
    if origin is None or local.inherited:
        return origin
    names, defer = queryset.query.deferred_loading
    return _Origin(
        origin.site,
        queryset.model.__name__,
        (),
        origin.noted,
        bool(names) or not defer,
    )


def _deferred_by_query(instance: Any) -> bool:
    """Whether the query that loaded ``instance`` left out some fields."""
    origin = _origin_of(instance)
    return origin is not None and origin.deferring


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


# The scope of the request being handled in this context, if any. A context
# variable, not a thread-local: asgiref copies it into the threads that run
# an async request's sync code and back, so it follows the request.
_request: ContextVar[int | None] = ContextVar("queryguard_django_request", default=None)


def _on_request_started(sender: Any, **kwargs: Any) -> None:
    # One scope per request (the test client's included), as a Session is
    # one for SQLAlchemy: queryguard_scope = session counts each on its own.
    _request.set(collector.RequestScope(next(errors.scopes)))


def _on_request_finished(sender: Any, **kwargs: Any) -> None:
    _request.set(None)


def _site() -> Any:
    """The call site; in an asgiref thread, the line that awaited the call."""
    return find_callsite() or _async_site.get()


def _site_origin() -> tuple[Any, Origin]:
    """``_site()`` and the call path (``callsite.find_origin``) in one walk.

    In an asgiref thread (the async ORM) the stack holds no line of the
    application: the site is the awaiting line's, and the origin unknown, so
    the event counts as the application's and with the passes of a loop.
    """
    site, origin = find_site_origin()
    if site is None:
        site = _async_site.get()
    return site, origin


def _scope_of(connection: Any) -> int | None:
    """The request being handled, else the connection the statement ran on."""
    scope = _request.get()
    return scope if scope is not None else errors.scope_of(connection)


def _scope(instance: Any) -> int | None:
    """The scope of the connection a lazy load of ``instance`` uses (mostly)."""
    connections = _connections
    if connections is None:
        return None
    return _scope_of(connections[instance._state.db or "default"])


# --- lazy loads -------------------------------------------------------------


def _relationship_event(
    label: str, instance: Any, scalar: bool, scope: int | None, origin: _Origin | None
) -> RelationshipLoadEvent:
    parent = _parent(origin)
    site, path = _site_origin()
    return RelationshipLoadEvent(
        label,
        _key(instance),
        site,
        scalar,
        scope,
        parent,
        blocks.current(),
        path.entry,
        path.test_code,
        orm=ORM_DJANGO,
        read_path=_read_path(),
    )


def _read_path() -> tuple[tuple[str, int], ...]:
    """The call path of a read (see ``RelationshipLoadEvent.read_path``):
    objects the test created and read through one helper at two lines of the
    test are two reads, not a loop, and so are two objects that one lookup
    helper gets and reads, called from two lines. In an asgiref thread (the
    async ORM) the path is the awaiting line's."""
    path = callsite.read_path()
    if not path:
        site = _async_site.get()
        if site is not None:
            path = tuple((s.filename, s.lineno) for s in (site, *site.callers))
    return path


def _begin(event: RelationshipLoadEvent) -> None:
    """Strict mode: raise at the lazy load that makes the N+1, before its SELECT."""
    full = collector.full_active()
    strict: BaseException | None = None
    raiser: collector.QueryCollector | None = None
    for target in full:
        if target.strict is not None:
            error = target.strict_check(collector.for_target(event, target))
            if strict is None and error is not None:
                strict, raiser = error, target
    if strict is None:
        return
    # a load that raises runs no SELECT: it is recorded here
    for target in full:
        target.add_relationship_load(collector.for_target(event, target))
    if raiser is not None:
        raiser.strict_raised = True
    # raised inside Django's ``except KeyError`` of an uncached relation: that
    # KeyError is no part of the report
    strict.__suppress_context__ = True
    raise strict


def _prepare(label: Callable[[], str], instance: Any, scalar: bool) -> _Pending | None:
    """The pending load of a relation of ``instance``; None after a plugin error.

    ``label`` and the scope are found inside the ``try``: a plugin error
    there must not reach the attribute access.
    """
    try:
        origin = _origin_of(instance)
        name = label()
        event = _relationship_event(name, instance, scalar, _scope(instance), origin)
        return event, _child_origin(origin, name, scalar)
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        _fail(exc, "Django integration (lazy load)")
        return None


def _run_load(pending: _Pending | None, call: Callable[[], Any]) -> Any:
    if pending is None or _machinery.get():
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
            lambda: f"{field.model.__name__}.{field.name}", instance, True
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
            lambda: f"{related.model.__name__}.{related.get_accessor_name()}",
            instance,
            True,
        )
        return _run_load(pending, lambda: original(self, instance, cls))

    return __get__


def _generic_get(
    original: Callable[..., Any], field_of: Callable[[Any], Any] | None
) -> Callable[..., Any]:
    """A generic foreign key: the object its content type and id point to."""

    def __get__(self: Any, instance: Any, cls: Any = None) -> Any:
        if instance is None or not _collectors or _failed():
            return original(self, instance, cls)
        field = self if field_of is None else field_of(self)
        ct_id = getattr(instance, field.model._meta.get_field(field.ct_field).attname)
        pk_val = getattr(instance, field.fk_field)
        if ct_id is None or pk_val is None:
            return original(self, instance, cls)  # no query
        db = instance._state.db
        # the cache first, as Django checks it: a cached target (or a cached
        # None) runs no query and looks no content type up by its id
        if _cached_target(field, instance, ct_id, pk_val, db):
            return original(self, instance, cls)
        # The content type next, outside the load: Django caches it, so a
        # loop looks it up once. Its errors are the application's own, as
        # Django would raise them in the call below.
        field.get_content_type(id=ct_id, using=db)
        # scalar False: the fix is prefetch_related, not select_related
        pending = _prepare(
            lambda: f"{field.model.__name__}.{field.name}", instance, False
        )
        return _run_load(pending, lambda: original(self, instance, cls))

    return __get__


def _cached_target(field: Any, instance: Any, ct_id: Any, pk_val: Any, db: Any) -> bool:
    """Whether the cached target still matches (Django then runs no query)."""
    if not field.is_cached(instance):
        return False
    target = field.get_cached_value(instance)
    if target is None:
        return True
    if field.get_content_type(obj=target, using=db).id != ct_id:
        return False
    return bool(target._meta.pk.to_python(pk_val) == target.pk)


def _deferred_get(original: Callable[..., Any]) -> Callable[..., Any]:
    # Django's own __get__ returns a loaded value from the instance's dict;
    # another wrapper installed before ours is always called
    own = getattr(original, "__module__", None) == "django.db.models.query_utils"

    def __get__(self: Any, instance: Any, cls: Any = None) -> Any:
        if own and instance is not None:
            # the value is loaded: what Django returns, without a second call
            # (save() reads every field through here)
            data = instance.__dict__
            name = self.field.attname
            if name in data:
                return data[name]
        if (
            instance is None
            or not _collectors
            or _failed()
            or (
                getattr(self.field, "generated", False)
                and not _deferred_by_query(instance)
            )
        ):
            # a GeneratedField without RETURNING is reloaded by Django itself
            # after save(): only one a query left out is a column load
            return original(self, instance, cls)
        try:
            site, path = _site_origin()
            event = ColumnLoadEvent(
                type(instance).__name__,
                (self.field.name,),
                CAUSE_DEFERRED,
                _key(instance),
                site,
                scope=_scope(instance),
                ignores=blocks.current(),
                entry=path.entry,
                test_code=path.test_code,
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
        return _mark_if_measuring(original(self), self)

    cls.get_queryset = get_queryset
    # many-to-many count() and exists() query the through table alone
    target = cls.__dict__.get("constrained_target")
    if isinstance(target, property) and target.fget is not None:
        cls.constrained_target = property(_constrained(target.fget))
    for name in _MANAGER_WRITES:
        method = cls.__dict__.get(name)
        if method is not None:
            setattr(cls, name, _writing_method(method))
    setattr(cls, _INSTRUMENTED, True)


def _constrained(original: Callable[[Any], Any]) -> Callable[[Any], Any]:
    def constrained_target(self: Any) -> Any:
        return _mark_if_measuring(original(self), self)

    return constrained_target


def _mark_if_measuring(queryset: Any, manager: Any) -> Any:
    """``queryset`` of ``manager``, marked as a read of the relation while
    something measures and no write of the manager runs (``_mark``)."""
    if queryset is not None and _collectors and not _writing.get() and not _failed():
        try:
            _mark(queryset, manager)
        except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
            _fail(exc, "Django integration (related manager)")
    return queryset


def _writing_method(original: Callable[..., Any]) -> Callable[..., Any]:
    # functools.wraps keeps alters_data and queryset_only (the function's
    # __dict__) visible to templates and managers
    @functools.wraps(original)
    def method(self: Any, *args: Any, **kwargs: Any) -> Any:
        if not _collectors:
            return original(self, *args, **kwargs)
        token = _writing.set(True)
        try:
            return original(self, *args, **kwargs)
        finally:
            _writing.reset(token)

    return method


def _receivers(send: Callable[..., Any]) -> Callable[..., Any]:
    """A model signal's ``send``: its receivers are the application's code,
    also inside a related manager's write or a delete (``_writing_method``,
    ``_in_unit``): their reads are reads, their writes are writes of their own."""

    @functools.wraps(send)
    def method(*args: Any, **kwargs: Any) -> Any:
        if not _writing.get() and _unit.get() is None:
            return send(*args, **kwargs)
        writing = _writing.set(False)
        unit = _unit.set(None)
        try:
            return send(*args, **kwargs)
        finally:
            _unit.reset(unit)
            _writing.reset(writing)

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
        if not _collectors:
            return original(self, *args, **kwargs)
        mark = self.query.__dict__.get(_MARK)
        if (
            mark is None
            or not isinstance(mark, _Load)
            or isinstance(self, writes)
            or _writing.get()
            or _failed()
        ):
            return original(self, *args, **kwargs)
        try:
            parent = _parent(mark.origin)
            site, path = _site_origin()
            event = RelationshipLoadEvent(
                mark.label,
                mark.key,
                site,
                False,
                _scope_of(self.connection),
                parent,
                blocks.current(),
                path.entry,
                path.test_code,
                orm=ORM_DJANGO,
                read_path=_read_path(),
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


# --- the async ORM ------------------------------------------------------------


def _is_orm_call(adapter: Any) -> bool:
    """Whether a SyncToAsync runs a method of a queryset, manager or model:
    the async ORM (aget(), acount(), async for, asave(), ...). Django's other
    uses (signals, middleware, closing a response) are left alone. A plain
    function of Django's ORM counts too: ``aiterator()`` runs a nested
    function of ``django.db.models.query`` per chunk."""
    func = getattr(adapter, "func", None)
    owner = getattr(func, "__self__", None)
    if owner is None:
        module = getattr(func, "__module__", None)
        return isinstance(module, str) and module.startswith("django.db.models.")
    models = sys.modules.get("django.db.models")
    if models is None:
        return False
    manager = sys.modules.get("django.db.models.manager")
    kinds: tuple[type, ...] = (models.QuerySet, models.Model)
    if manager is not None:
        kinds = (*kinds, manager.BaseManager)
    return isinstance(owner, kinds)


def _sync_to_async_call(original: Callable[..., Any]) -> Callable[..., Any]:
    """Note the awaiting application line for the thread the call runs in."""

    @functools.wraps(original)
    async def __call__(self: Any, *args: Any, **kwargs: Any) -> Any:
        if not collector._active or _failed() or not _is_orm_call(self):
            return await original(self, *args, **kwargs)
        try:
            site = find_callsite()
        except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
            _fail(exc, "Django integration (async call site)")
            site = None
        token = _async_site.set(site)
        try:
            return await original(self, *args, **kwargs)
        finally:
            _async_site.reset(token)

    return __call__


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
