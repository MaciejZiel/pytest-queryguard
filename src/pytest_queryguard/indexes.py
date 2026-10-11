"""Missing-index check: plan each SELECT with sequential scans disabled.

See docs/design-0.2.md for the reasoning behind each rule here.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any

from .callsite import find_callsite
from .collector import QueryCollector
from .detectors import normalize
from .events import ScanEvent, ScanIssue

MIN_SERVER_VERSION = 16
_SAVEPOINT = "queryguard_explain"
_SETTINGS = (
    "SELECT set_config('enable_seqscan', 'off', true), "
    "set_config('lock_timeout', '1s', true), "
    "set_config('statement_timeout', '5s', true)"
)
_EXPLAIN = "EXPLAIN (GENERIC_PLAN, VERBOSE, FORMAT JSON) "

# ------------------------------------------------------------ statement text

_CHECKED = re.compile(r"[\s(]*(?:SELECT|WITH)\b", re.IGNORECASE)

# Tokens that are copied unchanged, then the placeholders that are rewritten.
_TOKEN = re.compile(
    r"""
      (?P<copy>
          (?<![\w$])[eE]'(?:[^'\\]|\\.|'')*'?     # E'...' with backslash escapes
        | '(?:[^']|'')*'?                         # '...'
        | "(?:[^"]|"")*"?                         # "quoted identifier"
        | (?<![\w$])\$\$.*?(?:\$\$|\Z)            # $$...$$
        | (?<![\w$])\$(?P<tag>[A-Za-z_]\w*)\$.*?(?:\$(?P=tag)\$|\Z)  # $tag$...$tag$
        | --[^\n]*                                # line comment
        | /\*.*?(?:\*/|\Z)                        # block comment
      )
    | (?P<named>%\((?P<name>[^)]*)\)s)
    | (?P<positional>%s)
    | (?P<percent>%%)
    """,
    re.VERBOSE | re.DOTALL,
)


def is_checked_statement(sql: str) -> bool:
    """Only SELECT statements (and WITH queries) are explained."""
    return _CHECKED.match(sql) is not None


def to_numbered(sql: str, paramstyle: str) -> str | None:
    """The statement with $1, $2, ... placeholders, or None for an unknown style."""
    if paramstyle == "numeric_dollar":
        return sql
    if paramstyle not in ("format", "pyformat"):
        return None
    numbers: dict[str, int] = {}
    count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        if match["copy"] is not None:
            return match["copy"]
        if match["percent"] is not None:
            return "%"
        if match["named"] is not None:
            name = match["name"]
            if name not in numbers:
                count += 1
                numbers[name] = count
            return f"${numbers[name]}"
        count += 1
        return f"${count}"

    return _TOKEN.sub(replace, sql)


# ---------------------------------------------------------------- plan walk

_SYSTEM_SCHEMAS = frozenset({"pg_catalog", "information_schema", "pg_toast"})
_JOIN_KEYS = ("Hash Cond", "Merge Cond", "Join Filter")
_INDEX_SCANS = ("Index Scan", "Index Only Scan")
_SUBPLANS = ("SubPlan", "InitPlan")


def find_scans(plan: Mapping[str, Any]) -> list[ScanIssue]:
    """Plan nodes that read a whole table although the statement narrows it."""
    found: list[ScanIssue] = []
    _walk(plan, (), found)
    return found


def _walk(node: object, joins: tuple[str, ...], found: list[ScanIssue]) -> None:
    if not isinstance(node, Mapping):
        return
    if node.get("Parent Relationship") in _SUBPLANS:
        joins = ()  # a subquery is planned on its own; outer joins do not apply
    issue = _issue(node, joins)
    if issue is not None:
        found.append(issue)
    own = tuple(c for c in (node.get(k) for k in _JOIN_KEYS) if isinstance(c, str))
    children = node.get("Plans")
    if isinstance(children, list):
        for child in children:
            _walk(child, own + joins, found)


def _text(node: Mapping[str, Any], key: str) -> str | None:
    value = node.get(key)
    return value if isinstance(value, str) and value else None


def _issue(node: Mapping[str, Any], joins: tuple[str, ...]) -> ScanIssue | None:
    table = _text(node, "Relation Name")
    schema = _text(node, "Schema")
    if table is None or schema in _SYSTEM_SCHEMAS:
        return None
    alias = _text(node, "Alias") or table
    node_type = node.get("Node Type")
    condition = _text(node, "Filter")
    if node_type == "Seq Scan":
        if condition is not None:
            return _make(schema, table, "Seq Scan", "filter", condition, alias)
        for join in joins:
            if _column_refs(_strip_literals(join), alias):
                return _make(schema, table, "Seq Scan", "join", join, alias)
        return None
    if node_type in _INDEX_SCANS and condition and not node.get("Index Cond"):
        # A whole index walked only to avoid the disabled sequential scan.
        index = _text(node, "Index Name")
        label = f"{node_type} using {index}" if index else str(node_type)
        return _make(schema, table, label, "filter", condition, alias)
    return None


def _make(
    schema: str | None, table: str, node: str, kind: str, condition: str, alias: str
) -> ScanIssue:
    return ScanIssue(
        schema, table, node, kind, condition, hint_column(condition, alias)
    )


# ---------------------------------------------------------------- fix hint

_LITERAL = re.compile(r"'(?:[^']|'')*'")
_OP = r"(?<![-<>=!~#@&|*+/%^])(?:<=|>=|=|<|>)(?![-<>=~#@&|])"
_CAST = r"""(?:::[\w ."\[\]]+?)?"""
_LEFT_OPERAND = re.compile(rf"\)*{_CAST}\)*\s*{_OP}\s")
_RIGHT_OPERAND = re.compile(rf"\)*{_CAST}\)*(?:$|\s+(?:AND|OR)\b)")
_BEFORE_RIGHT = re.compile(rf"{_OP}\s+\(*$")


def _strip_literals(condition: str) -> str:
    return _LITERAL.sub("''", condition)


def _column_refs(text: str, alias: str) -> list[re.Match[str]]:
    quoted = '"' + alias.replace('"', '""') + '"'
    names = "|".join(re.escape(a) for a in dict.fromkeys((alias, quoted)))
    pattern = rf'(?<![\w$."])(?:{names})\.(?P<col>"(?:[^"]|"")+"|[A-Za-z_][\w$]*)'
    return list(re.finditer(pattern, text))


def _inside_function(text: str, start: int) -> bool:
    depth = 0
    for i in range(start - 1, -1, -1):
        char = text[i]
        if char == ")":
            depth += 1
        elif char == "(":
            if depth:
                depth -= 1
            elif i and (text[i - 1].isalnum() or text[i - 1] == "_"):
                return True
    return False


def _compared(text: str, start: int, end: int) -> bool:
    before, after = text[:start], text[end:]
    if before.endswith("(") and _LEFT_OPERAND.match(after):
        return True
    return bool(_BEFORE_RIGHT.search(before)) and bool(_RIGHT_OPERAND.match(after))


def hint_column(condition: str, alias: str) -> str | None:
    """The one column an index on the scanned table would serve, if obvious."""
    text = _strip_literals(condition)
    refs = _column_refs(text, alias)
    columns = {ref["col"] for ref in refs}
    if len(columns) != 1:
        return None
    for ref in refs:
        if _inside_function(text, ref.start()) or not _compared(
            text, ref.start(), ref.end()
        ):
            return None
    return columns.pop()


# PostgreSQL's reserved keywords, which need quotes as table names.
_RESERVED_WORDS = """
    all analyse analyze and any array as asc asymmetric authorization binary both
    case cast check collate collation column concurrently constraint create cross
    current_catalog current_date current_role current_schema current_time
    current_timestamp current_user default deferrable desc distinct do else end
    except false fetch for foreign freeze from full grant group having ilike in
    initially inner intersect into is isnull join lateral leading left like limit
    localtime localtimestamp natural not notnull null offset on only or order outer
    overlaps placing primary references returning right select session_user
    similar some symmetric system_user table tablesample then to trailing true
    union unique user using variadic verbose when where window with
"""
_RESERVED = frozenset(_RESERVED_WORDS.split())
_PLAIN_NAME = re.compile(r"[a-z_][a-z0-9_$]*")


def quote_ident(name: str) -> str:
    if _PLAIN_NAME.fullmatch(name) and name not in _RESERVED:
        return name
    return '"' + name.replace('"', '""') + '"'


def table_label(schema: str | None, table: str) -> str:
    if schema in (None, "public"):
        return quote_ident(table)
    return f"{quote_ident(schema)}.{quote_ident(table)}"


def is_ignored(issue: ScanIssue, patterns: Iterable[str]) -> bool:
    names = [issue.table]
    if issue.schema is not None:
        names.append(f"{issue.schema}.{issue.table}")
    return any(fnmatchcase(name, p) for p in patterns for name in names)


def select_findings(
    scans: Sequence[ScanEvent], ignore: Iterable[str]
) -> list[ScanEvent]:
    """One event per table and condition, first occurrence first, minus ignored."""
    patterns = tuple(ignore)
    seen: set[tuple[object, ...]] = set()
    found: list[ScanEvent] = []
    for scan in scans:
        issue = scan.issue
        key = (issue.schema, issue.table, issue.node, issue.kind, issue.condition)
        if key in seen or is_ignored(issue, patterns):
            continue
        seen.add(key)
        found.append(scan)
    return found


# ---------------------------------------------------------------- EXPLAIN


@dataclass(frozen=True, slots=True)
class ExplainError:
    message: str


def _error_message(exc: Exception) -> str:
    detail = exc.args[0] if exc.args else exc
    if isinstance(detail, Mapping) and "M" in detail:  # pg8000 error fields
        return str(detail["M"])
    lines = str(detail).strip().splitlines()
    return lines[0] if lines else type(exc).__name__


def _read_plan(value: object) -> Mapping[str, Any] | None:
    if isinstance(value, (str, bytes)):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if isinstance(value, list) and value and isinstance(value[0], Mapping):
        plan = value[0].get("Plan")
        if isinstance(plan, Mapping):
            return plan
    return None


def run_explain(dbapi_connection: Any, sql: str) -> Mapping[str, Any] | ExplainError:
    """Plan ``sql`` on the test's own connection without changing its state.

    Database errors from EXPLAIN come back as ExplainError. An error while
    opening or rolling back the savepoint is raised: the connection's state
    is then unknown, and the caller stops checking.
    """
    if getattr(dbapi_connection, "autocommit", False):
        start, undo = ["BEGIN"], ["ROLLBACK"]
    else:
        start = [f"SAVEPOINT {_SAVEPOINT}"]
        undo = [
            f"ROLLBACK TO SAVEPOINT {_SAVEPOINT}",
            f"RELEASE SAVEPOINT {_SAVEPOINT}",
        ]
    cursor = dbapi_connection.cursor()
    try:
        for sql_text in start:
            cursor.execute(sql_text)
        row: Any = None
        error: ExplainError | None = None
        try:
            cursor.execute(_SETTINGS)
            cursor.execute(_EXPLAIN + sql)
            row = cursor.fetchone()
        except Exception as exc:  # noqa: BLE001  # any database error from EXPLAIN
            error = ExplainError(_error_message(exc))
        for sql_text in undo:
            cursor.execute(sql_text)
    finally:
        cursor.close()
    if error is not None:
        return error
    plan = _read_plan(row[0] if row else None)
    if plan is None:
        return ExplainError(f"unexpected EXPLAIN output: {row!r:.100}")
    return plan


# ---------------------------------------------------------------- listener


def unsupported_reason(dialect: Any) -> str | None:
    """Why statements from this dialect cannot be checked, or None."""
    name = getattr(dialect, "name", "?")
    if name != "postgresql":
        return (
            f"{name} statements were not checked: "
            f"the index check needs PostgreSQL {MIN_SERVER_VERSION} or newer"
        )
    driver = getattr(dialect, "driver", "?")
    if getattr(dialect, "is_async", False):
        return (
            f"statements from the async driver {driver} were not checked: "
            "the index check supports sync engines only"
        )
    version = getattr(dialect, "server_version_info", None)
    if version and isinstance(version[0], int) and version[0] < MIN_SERVER_VERSION:
        shown = ".".join(str(part) for part in version)
        return (
            f"PostgreSQL {shown} statements were not checked: "
            f"the index check needs PostgreSQL {MIN_SERVER_VERSION} or newer"
        )
    paramstyle = getattr(dialect, "paramstyle", "?")
    if to_numbered("", paramstyle) is None:
        return (
            f"statements from {driver} were not checked: "
            f"its paramstyle {paramstyle!r} is not supported by the index check"
        )
    return None


def check(
    conn: Any, statement: str, executemany: bool, targets: Sequence[QueryCollector]
) -> None:
    """Explain one executed statement for the collectors that want it."""
    if executemany or not is_checked_statement(statement):
        return
    dialect = conn.dialect
    reason = unsupported_reason(dialect)
    if reason is not None:
        for target in targets:
            target.index_notices[reason] = None
        return
    key = (id(conn.engine), normalize(statement))
    fresh = [target for target in targets if key not in target.explained]
    if not fresh:
        return
    for target in fresh:
        target.explained.add(key)
    numbered = to_numbered(statement, dialect.paramstyle)
    assert numbered is not None  # unsupported_reason() checked the paramstyle
    outcome = run_explain(conn.connection.dbapi_connection, numbered)
    if isinstance(outcome, ExplainError):
        for target in fresh:
            target.explain_errors.append((outcome.message, statement))
        return
    issues = find_scans(outcome)
    if not issues:
        return
    site = find_callsite()
    for issue in issues:
        scan = ScanEvent(issue, statement, site)
        for target in fresh:
            target.scans.append(scan)
