"""Verify a suggested index with the hypopg extension.

hypopg keeps hypothetical indexes in the memory of the database session, not in
the catalogs, so a ROLLBACK does not remove them and only a plain EXPLAIN (no
ANALYZE, so also EXPLAIN (GENERIC_PLAN)) on the same session sees them.
``verify`` therefore creates them, plans the statement again and resets them
before it returns, whatever happened in between. See docs/index-advisor.md.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .events import Verification

_SAVEPOINT = "queryguard_hypopg"
_PROBE = (
    "SELECT (SELECT extversion FROM pg_extension WHERE extname = 'hypopg'), "
    "(SELECT default_version FROM pg_available_extensions WHERE name = 'hypopg')"
)
_SCHEMA = (
    "SELECT extnamespace::regnamespace::text FROM pg_extension WHERE extname = 'hypopg'"
)
# The extension lives in one schema, which need not be on the connection's
# search_path (an application may set its own), so its functions are qualified.
# One constant text per database, so asyncpg caches one statement per kind.
_CREATE = (
    "SELECT indexname, {schema}.hypopg_relation_size(indexrelid) "
    "FROM {schema}.hypopg_create_index({ddl})"
)
_RESET = "SELECT {schema}.hypopg_reset()"

MODES = ("auto", "on", "off")

# (installed version, available version) of the extension
Probe = tuple[str | None, str | None]


def literal(text: str) -> str | None:
    """``text`` as an SQL string literal, or None when that is not safe to do."""
    if "\\" in text or "\x00" in text:
        return None
    return "'" + text.replace("'", "''") + "'"


def probe(cursor: Any) -> Probe:
    cursor.execute(_PROBE)
    row = cursor.fetchone()
    installed, available = (row[0], row[1]) if row else (None, None)
    return (
        installed if isinstance(installed, str) else None,
        available if isinstance(available, str) else None,
    )


def notice(probed: Probe, mode: str, server_major: int | None) -> str | None:
    """The one-time notice for a statement that could not be verified."""
    installed, available = probed
    if installed is not None or mode == "off":
        return None
    package = f"postgresql-{server_major}-hypopg" if server_major else "hypopg"
    how = "set queryguard_hypopg = off to silence this notice"
    if available is not None:
        return (
            "index advisor: suggested indexes were not verified: the hypopg "
            "extension is available but not installed in this database. Run "
            "CREATE EXTENSION hypopg (needs a superuser), or set "
            "queryguard_hypopg = on to let the check create it inside its own "
            f"rolled-back savepoint; or {how}"
        )
    return (
        "index advisor: suggested indexes were not verified: the hypopg "
        "extension is not available on this server. Install it (Debian and "
        f"Ubuntu: apt install {package}; see https://github.com/HypoPG/hypopg), "
        f"then run CREATE EXTENSION hypopg in the test database; or {how}"
    )


def _fetch(cursor: Any, sql: str, *params: Any) -> Any:
    cursor.execute(sql, *params) if params else cursor.execute(sql)
    return cursor.fetchone()


def _cost(plan: Mapping[str, Any]) -> float | None:
    cost = plan.get("Total Cost")
    return float(cost) if isinstance(cost, (int, float)) else None


def _scan_of(plan: Mapping[str, Any], name: str) -> str | None:
    """The node type that reads the index ``name``, or None when none does."""
    if plan.get("Index Name") == name:
        node_type = plan.get("Node Type")
        return node_type if isinstance(node_type, str) else "Index Scan"
    children = plan.get("Plans")
    for child in children if isinstance(children, list) else ():
        if isinstance(child, Mapping) and (found := _scan_of(child, name)):
            return found
    return None


def _message(exc: Exception) -> str:
    detail = exc.args[0] if exc.args else exc
    if isinstance(detail, Mapping) and "M" in detail:  # pg8000 error fields
        return str(detail["M"])
    lines = str(detail).strip().splitlines()
    return lines[0] if lines else type(exc).__name__


def verify(
    cursor: Any,
    plan_with: Callable[[bool], Mapping[str, Any]],
    ddls: Sequence[str],
    *,
    asyncpg: bool,
    create_extension: bool = False,
) -> list[Verification]:
    """Check each ``CREATE INDEX`` in ``ddls`` on its own against the statement.

    ``plan_with(True)`` plans the statement with sequential scans allowed and
    ``plan_with(False)`` with them off. Must run inside a transaction or
    savepoint; opens a nested one of its own. Every hypothetical index is
    reset before this returns. A database error becomes the ``error`` of the
    affected results; an error on the savepoint itself is raised.
    """
    results = [Verification(error="not run") for _ in ddls]
    cursor.execute(f"SAVEPOINT {_SAVEPOINT}")
    reset = ""
    try:
        try:
            if create_extension:
                cursor.execute("CREATE EXTENSION IF NOT EXISTS hypopg")
            schema = _fetch(cursor, _SCHEMA)[0]
            reset = _RESET.format(schema=schema)
            before = _cost(plan_with(True))
        except Exception as exc:  # noqa: BLE001  # reported on every result
            _recover(cursor, reset)
            return [Verification(error=_message(exc)) for _ in ddls]
        for i, ddl in enumerate(ddls):
            try:
                results[i] = _verify_one(
                    cursor, plan_with, ddl, before, asyncpg, schema, reset
                )
            except Exception as exc:  # noqa: BLE001  # one index failed
                _recover(cursor, reset)
                results[i] = Verification(error=_message(exc))
    finally:
        try:
            if reset:
                cursor.execute(reset)
        finally:
            cursor.execute(f"RELEASE SAVEPOINT {_SAVEPOINT}")
    return results


def _recover(cursor: Any, reset: str) -> None:
    """Leave the aborted state of a failed statement and drop what was created."""
    cursor.execute(f"ROLLBACK TO SAVEPOINT {_SAVEPOINT}")
    if reset:
        cursor.execute(reset)


def _verify_one(
    cursor: Any,
    plan_with: Callable[[bool], Mapping[str, Any]],
    ddl: str,
    before: float | None,
    asyncpg: bool,
    schema: str,
    reset: str,
) -> Verification:
    if asyncpg:
        row = _fetch(cursor, _CREATE.format(schema=schema, ddl="$1"), ddl)
    else:
        text = literal(ddl)
        if text is None:
            return Verification(error="the definition cannot be quoted")
        row = _fetch(cursor, _CREATE.format(schema=schema, ddl=text))
    if not row:
        return Verification(error="hypopg created no index")
    name, size = str(row[0]), row[1]
    scan = _scan_of(plan_with(False), name)
    after = _cost(plan_with(True))
    cursor.execute(reset)
    return Verification(
        used=scan is not None,
        cost_before=before,
        cost_after=after,
        scan=scan,
        size=int(size) if isinstance(size, int) else None,
    )
