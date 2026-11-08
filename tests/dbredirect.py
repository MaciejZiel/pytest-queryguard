"""Run the SQLite tests on a MySQL or MariaDB server instead.

Most tests create their database with ``create_engine("sqlite://")`` or a
SQLite file under ``tmp_path``. With ``QUERYGUARD_TEST_DB_URL`` set to the URL
of a server, without a database (``mysql+pymysql://root:secret@127.0.0.1:3306``),
tests/conftest.py calls :func:`install` before any test module is imported,
and every SQLite URL given to ``create_engine`` or ``create_async_engine`` in
this process becomes a fresh database on that server:

* an in-memory URL (``sqlite://``) a new database for each engine, since each
  in-memory SQLite engine is a database of its own;
* a file URL one database per file, shared by the sync and the async engine
  of that file, as the file is. The file is created empty and stands for the
  database: once it is deleted, the next engine gets a new database.

The async engines use aiomysql. A ``String`` without a length, which SQLite
accepts and MySQL does not, is created as ``VARCHAR(255)``. Nothing else is
translated: a test that depends on SQLite itself (its SQL text, ``PRAGMA``,
the ``sqlite3`` module) checks :func:`active` and is made dialect-aware or
skipped with the reason. Sessions that pytester starts in a subprocess do not
load this module and keep SQLite.

The databases are named ``qgt_<pid>_<n>`` and dropped when the process exits.
The user of the URL must be allowed to create and drop databases.
"""

from __future__ import annotations

import atexit
import contextlib
import itertools
import os
import re
import threading
from collections.abc import Iterator
from typing import Any

ENV = "QUERYGUARD_TEST_DB_URL"
_ASYNC_DRIVERS = {"pymysql": "aiomysql", "mysqldb": "aiomysql", "aiomysql": "aiomysql"}

_server: Any = None  # sqlalchemy URL of the server, without a database
_admin: Any = None  # an engine on the server, for CREATE and DROP DATABASE
_by_path: dict[str, str] = {}
_created: list[str] = []
_counter = itertools.count(1)
_lock = threading.Lock()
_paused = False  # set for the tests marked sqlite_only, see tests/conftest.py


def active() -> bool:
    """Whether the SQLite engines of this process go to a server."""
    return _server is not None and not _paused


@contextlib.contextmanager
def paused() -> Iterator[None]:
    """Keep the engines created inside on SQLite."""
    global _paused
    before, _paused = _paused, True
    try:
        yield
    finally:
        _paused = before


def kind() -> str:
    """``"sqlite"``, or the dialect of the server: ``"mysql"`` or ``"mariadb"``."""
    if _admin is None:
        return "sqlite"
    with _admin.connect() as conn:
        return "mariadb" if conn.dialect.is_mariadb else "mysql"


def _database_for(url: Any) -> str:
    path = url.database
    with _lock:
        if path and path != ":memory:" and "mode=memory" not in str(url):
            key = os.path.abspath(path)
            # The file stands for the database: when it is gone (pytester
            # reuses its basetemp), so is the database.
            if key in _by_path and os.path.exists(key):
                return _by_path[key]
        else:
            key = None
        name = f"qgt_{os.getpid()}_{next(_counter)}"
        _admin_execute(f"CREATE DATABASE {name}")
        _created.append(name)
        if key is not None:
            _by_path[key] = name
            if os.path.isdir(os.path.dirname(key)):
                open(key, "a").close()
        return name


def _admin_execute(statement: str) -> None:
    """On a DB-API connection of its own: the plugin must not count it."""
    raw = _admin.raw_connection()
    try:
        with raw.cursor() as cursor:
            cursor.execute(statement)
    finally:
        raw.close()


def _redirect(url: Any, kwargs: dict[str, Any], *, asynchronous: bool) -> Any:
    """The server URL that replaces ``url``, or None to keep ``url``."""
    from sqlalchemy.engine import make_url

    url = make_url(url)
    # a creator hands over its own sqlite3 connection
    if _paused or url.get_backend_name() != "sqlite" or "creator" in kwargs:
        return None
    # connect_args and pool options of sqlite3 (check_same_thread, timeout,
    # uri) mean nothing to the MySQL drivers, and some are errors there.
    kwargs.pop("connect_args", None)
    driver = _server.get_driver_name()
    if asynchronous:
        driver = _ASYNC_DRIVERS.get(driver, driver)
    return _server.set(
        drivername=f"{_server.get_backend_name()}+{driver}",
        database=_database_for(url),
    )


# SQLite numbers the rows of an INTEGER PRIMARY KEY column by itself; MySQL
# needs AUTO_INCREMENT for that. Only in a CREATE TABLE written as text.
_ROWID = re.compile(
    r"\bINTEGER\s+PRIMARY\s+KEY(\s+AUTOINCREMENT)?\b(?!\s+AUTO_INCREMENT)",
    re.IGNORECASE,
)


def _ddl(
    conn: Any,
    cursor: Any,
    statement: str,
    parameters: Any,
    context: Any,
    executemany: bool,
) -> tuple[str, Any]:
    if statement.lstrip()[:12].upper() == "CREATE TABLE":
        statement = _ROWID.sub("INTEGER PRIMARY KEY AUTO_INCREMENT", statement)
    return statement, parameters


def install(server_url: str) -> None:
    """Send the SQLite engines of this process to ``server_url``."""
    global _server, _admin
    import sqlalchemy
    import sqlalchemy.engine
    import sqlalchemy.engine.create
    import sqlalchemy.ext.asyncio
    import sqlalchemy.ext.asyncio.engine
    from sqlalchemy import String, Unicode, event
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.compiler import compiles

    if _server is not None:  # installed already, in this process
        return
    server = make_url(server_url)
    if server.get_backend_name() not in ("mysql", "mariadb"):
        raise ValueError(f"{ENV} must be a MySQL or MariaDB URL, not {server_url!r}")
    original = sqlalchemy.engine.create.create_engine
    original_async = sqlalchemy.ext.asyncio.engine.create_async_engine
    _server = server.set(database=None)
    _admin = original(_server, isolation_level="AUTOCOMMIT", pool_pre_ping=True)

    def create_engine(url: Any, **kwargs: Any) -> Any:
        target = _redirect(url, kwargs, asynchronous=False)
        engine = original(url if target is None else target, **kwargs)
        if target is not None:
            event.listen(engine, "before_cursor_execute", _ddl, retval=True)
        return engine

    def create_async_engine(url: Any, **kwargs: Any) -> Any:
        target = _redirect(url, kwargs, asynchronous=True)
        engine = original_async(url if target is None else target, **kwargs)
        if target is not None:
            event.listen(engine.sync_engine, "before_cursor_execute", _ddl, retval=True)
        return engine

    for module in (sqlalchemy, sqlalchemy.engine, sqlalchemy.engine.create):
        module.create_engine = create_engine  # type: ignore[attr-defined]
    for module in (sqlalchemy.ext.asyncio, sqlalchemy.ext.asyncio.engine):
        module.create_async_engine = create_async_engine  # type: ignore[attr-defined]

    @compiles(String, "mysql")
    @compiles(Unicode, "mysql")
    def _string(type_: Any, compiler: Any, **kw: Any) -> str:
        if type_.length is None:
            type_ = type_.__class__(255, collation=type_.collation)
        return compiler.visit_VARCHAR(type_, **kw)  # type: ignore[no-any-return]

    atexit.register(_drop_all)


def _drop_all() -> None:
    if _admin is None:
        return
    raw = _admin.raw_connection()
    try:
        with raw.cursor() as cursor:
            # a connection the tests left open may hold a lock on a table
            cursor.execute("SET SESSION lock_wait_timeout = 5")
            for name in _created:
                try:
                    cursor.execute(f"DROP DATABASE IF EXISTS {name}")
                except Exception as error:  # noqa: BLE001
                    print(f"dbredirect: could not drop {name}: {error}")
    finally:
        raw.close()
        _admin.dispose()
