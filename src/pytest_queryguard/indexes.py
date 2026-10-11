"""Missing-index check: plan each SELECT with sequential scans disabled.

See docs/design-0.2.md for the reasoning behind each rule here.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Any

from .callsite import find_callsite
from .collector import QueryCollector
from .detectors import collapse_lists
from .events import ScanEvent, ScanIssue

MIN_SERVER_VERSION = 16
_SAVEPOINT = "queryguard_explain"
_SETTINGS = (
    "SELECT set_config('enable_seqscan', 'off', true), "
    "set_config('lock_timeout', '1s', true), "
    "set_config('statement_timeout', '5s', true), "
    "current_schema()"
)
_EXPLAIN = "EXPLAIN (GENERIC_PLAN, VERBOSE, FORMAT JSON) "

# ------------------------------------------------------------ statement text

# Leading whitespace, parentheses and comments (sqlcommenter tags), then the verb.
_CHECKED = re.compile(
    r"(?:\s+|\(|--[^\n]*(?:\n|\Z)|/\*.*?\*/)*(?:SELECT|WITH)\b",
    re.IGNORECASE | re.DOTALL,
)

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


def to_numbered(
    sql: str, paramstyle: str, *, unescape_quoted: bool = False
) -> str | None:
    """The statement with $1, $2, ... placeholders, or None for an unknown style.

    ``unescape_quoted`` turns ``%%`` into ``%`` inside quoted text and
    comments too, as psycopg and psycopg2 do; pg8000 leaves them alone.
    """
    if paramstyle == "numeric_dollar":
        return sql
    if paramstyle not in ("format", "pyformat"):
        return None
    numbers: dict[str, int] = {}
    count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        if match["copy"] is not None:
            copy = match["copy"]
            return copy.replace("%%", "%") if unescape_quoted else copy
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
_INDEX_SCANS = ("Index Scan", "Index Only Scan")
_BITMAP_SCAN = "Bitmap Index Scan"
# The PostgreSQL release that can skip-scan the leading column of an index.
SKIP_SCAN_VERSION = 18

# (schema, index name) -> name of the index's first key column
LeadingColumns = Mapping[tuple[str, str], str]


@dataclass(frozen=True, slots=True)
class _Relation:
    schema: str | None
    table: str
    alias: str


def find_scans(
    plan: Mapping[str, Any],
    current_schema: str | None = None,
    leading: LeadingColumns | None = None,
) -> list[ScanIssue]:
    """Plan nodes that read a whole table, or a whole index, to apply a condition.

    Tables in ``current_schema`` (``public`` when unknown) are named without
    their schema, so the fix hint works as written on that connection.
    ``leading`` enables the check for an index condition on a trailing
    column of a multicolumn index.
    """
    found: list[ScanIssue] = []
    _walk(plan, None, current_schema or "public", leading or {}, found)
    return found


def index_names(plan: Mapping[str, Any]) -> set[tuple[str, str]]:
    """(schema, index) of every index scan in the plan that has an Index Cond."""
    names: set[tuple[str, str]] = set()

    def visit(node: object, heap: _Relation | None) -> None:
        if not isinstance(node, Mapping):
            return
        rel = _relation(node)
        index = _text(node, "Index Name")
        owner = rel or heap
        if index and node.get("Index Cond") and owner is not None and owner.schema:
            names.add((owner.schema, index))
        below = rel if node.get("Node Type") == "Bitmap Heap Scan" else heap
        for child in _children(node):
            visit(child, below)

    visit(plan, None)
    return names


def _children(node: Mapping[str, Any]) -> list[object]:
    children = node.get("Plans")
    return children if isinstance(children, list) else []


def _text(node: Mapping[str, Any], key: str) -> str | None:
    value = node.get(key)
    return value if isinstance(value, str) and value else None


def _relation(node: Mapping[str, Any]) -> _Relation | None:
    table = _text(node, "Relation Name")
    if table is None:
        return None
    return _Relation(_text(node, "Schema"), table, _text(node, "Alias") or table)


def _walk(
    node: object,
    heap: _Relation | None,
    current: str,
    leading: LeadingColumns,
    found: list[ScanIssue],
) -> None:
    if not isinstance(node, Mapping):
        return
    rel = _relation(node)
    issue = _issue(node, rel or heap, current, leading)
    if issue is not None:
        found.append(issue)
    # A Bitmap Index Scan names no table; it reads the one of its heap scan.
    below = rel if node.get("Node Type") == "Bitmap Heap Scan" else heap
    for child in _children(node):
        _walk(child, below, current, leading, found)


def _issue(
    node: Mapping[str, Any],
    rel: _Relation | None,
    current: str,
    leading: LeadingColumns,
) -> ScanIssue | None:
    if rel is None or rel.schema in _SYSTEM_SCHEMAS:
        return None
    node_type = node.get("Node Type")
    index = _text(node, "Index Name")
    label = f"{node_type} using {index}" if index else str(node_type)
    if node_type == "Seq Scan":
        # Rule 1: the table is read in full to apply its filter.
        condition = _text(node, "Filter")
        if condition is None:
            return None
        return _make(rel, "Seq Scan", "filter", condition, current)
    if node_type not in (*_INDEX_SCANS, _BITMAP_SCAN):
        return None
    index_cond = _text(node, "Index Cond")
    if index_cond is None:
        # Rule 2: a whole index walked to avoid the disabled sequential scan,
        # for example the primary key for ORDER BY id LIMIT n. Pagination with
        # a range or IS NULL filter does that on purpose, so only an equality
        # on one column, which an index would serve directly, is reported.
        condition = _text(node, "Filter")
        if node_type == _BITMAP_SCAN or condition is None:
            return None
        if equality_column(condition, rel.alias) is None:
            return None
        return _make(rel, label, "filter", condition, current)
    # Rule 3: the index condition uses only trailing columns of a multicolumn
    # index, which PostgreSQL before 18 serves by reading the whole index.
    first = leading.get((rel.schema or "", index or ""))
    if first is None:
        return None
    columns = {_unquote(ref["col"]) for ref in _column_refs(index_cond, rel.alias)}
    if first in columns or equality_column(index_cond, rel.alias) is None:
        return None
    return _make(rel, label, "index condition", index_cond, current)


def _make(
    rel: _Relation, node: str, kind: str, condition: str, current: str
) -> ScanIssue:
    column = hint_column(condition, rel.alias)
    return ScanIssue(
        rel.schema,
        rel.table,
        node,
        kind,
        condition,
        column,
        show_schema=rel.schema not in (None, current),
        expression=column is None and _needs_expression(condition, rel.alias),
    )


# ---------------------------------------------------------------- fix hint

_LITERAL = re.compile(r"'(?:[^']|'')*'")
_OP = r"(?<![-<>=!~#@&|*+/%^])(?P<op><=|>=|=|<|>)(?![-<>=~#@&|])"
# The only cast an index on the plain column still serves: varchar or char
# compared as text, which the plan prints as (col)::text.
_TEXT_CAST = r"(?:::text)?"
_LEFT_OPERAND = re.compile(rf"\)*{_TEXT_CAST}\)*\s*{_OP}\s")
_RIGHT_OPERAND = re.compile(rf"\)*{_TEXT_CAST}\)*(?:$|\s+(?:AND|OR)\b)")
_BEFORE_RIGHT = re.compile(rf"{_OP}\s+\(*$")
_OTHER_CAST = re.compile(r"\)*::(?!text\b)")


def _strip_literals(condition: str) -> str:
    return _LITERAL.sub("''", condition)


def _unquote(name: str) -> str:
    if name.startswith('"'):
        return name[1:-1].replace('""', '"')
    return name


def _column_refs(text: str, alias: str) -> list[re.Match[str]]:
    quoted = '"' + alias.replace('"', '""') + '"'
    names = "|".join(re.escape(a) for a in dict.fromkeys((alias, quoted)))
    pattern = rf'(?<![\w$."])(?:{names})\.(?P<col>"(?:[^"]|"")+"|[A-Za-z_][\w$]*)'
    return list(re.finditer(pattern, _strip_literals(text)))


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


def _operator(text: str, start: int, end: int) -> str | None:
    """The comparison a plain column reference takes part in, if any."""
    if _inside_function(text, start):
        return None
    before, after = text[:start], text[end:]
    if before.endswith("("):
        left = _LEFT_OPERAND.match(after)
        if left:
            return left["op"]
    right = _BEFORE_RIGHT.search(before)
    if right and _RIGHT_OPERAND.match(after):
        return right["op"]
    return None


def _comparisons(condition: str, alias: str) -> tuple[str, set[str]] | None:
    """(column, operators) when the condition compares exactly one plain column."""
    text = _strip_literals(condition)
    refs = _column_refs(text, alias)
    columns = {ref["col"] for ref in refs}
    if len(columns) != 1:
        return None
    ops: set[str] = set()
    for ref in refs:
        op = _operator(text, ref.start(), ref.end())
        if op is None:
            return None
        ops.add(op)
    return columns.pop(), ops


def hint_column(condition: str, alias: str) -> str | None:
    """The one column an index on the scanned table would serve, if obvious."""
    found = _comparisons(condition, alias)
    return None if found is None else found[0]


def equality_column(condition: str, alias: str) -> str | None:
    """The column when the condition is only equalities (= or = ANY) on it."""
    found = _comparisons(condition, alias)
    if found is None or found[1] != {"="}:
        return None
    return found[0]


def _needs_expression(condition: str, alias: str) -> bool:
    """True when a column is wrapped in a function or cast an index cannot see."""
    text = _strip_literals(condition)
    return any(
        _inside_function(text, ref.start()) or _OTHER_CAST.match(text, ref.end())
        for ref in _column_refs(text, alias)
    )


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


def table_label(issue: ScanIssue) -> str:
    """The table as the fix hint should name it."""
    if issue.schema is None or not issue.show_schema:
        return quote_ident(issue.table)
    return f"{quote_ident(issue.schema)}.{quote_ident(issue.table)}"


def is_ignored(issue: ScanIssue, patterns: Iterable[str]) -> bool:
    names = [issue.table]
    if issue.schema is not None:
        names.append(f"{issue.schema}.{issue.table}")
    return any(fnmatchcase(name, p) for p in patterns for name in names)


def select_findings(
    scans: Sequence[ScanEvent], ignore: Iterable[str]
) -> list[ScanEvent]:
    """One event per missing index, first occurrence first, minus ignored.

    Scans are the same finding when they name the same column of the same
    table (the same query under two aliases), or else the same condition.
    """
    patterns = tuple(ignore)
    seen: set[tuple[object, ...]] = set()
    found: list[ScanEvent] = []
    for scan in scans:
        issue = scan.issue
        key: tuple[object, ...]
        if issue.column is not None:
            key = (issue.schema, issue.table, issue.column)
        else:
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


@dataclass(frozen=True, slots=True)
class Explained:
    plan: Mapping[str, Any]
    current_schema: str | None
    leading: LeadingColumns = field(default_factory=dict)


class SavepointError(Exception):
    """The savepoint around EXPLAIN could not be opened or rolled back."""


def _sql_literal(text: str) -> str | None:
    # Only for names read from the plan; backslashes would depend on
    # standard_conforming_strings, so such names are not looked up.
    if "\\" in text or "\x00" in text:
        return None
    return "'" + text.replace("'", "''") + "'"


def _leading_lookup(names: Iterable[tuple[str, str]]) -> str | None:
    pairs = []
    for schema, index in sorted(names):
        schema_sql, index_sql = _sql_literal(schema), _sql_literal(index)
        if schema_sql is not None and index_sql is not None:
            pairs.append(f"({schema_sql}, {index_sql})")
    if not pairs:
        return None
    return (
        "SELECT n.nspname, c.relname, a.attname "
        "FROM pg_index i "
        "JOIN pg_class c ON c.oid = i.indexrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = i.indkey[0] "
        f"WHERE (n.nspname, c.relname) IN ({', '.join(pairs)})"
    )


# (schema, index) -> first key column, or None for an expression index or an
# index the lookup did not find; filled per test, as indexes can change.
KnownColumns = MutableMapping[tuple[str, str], str | None]


def _read_leading(
    cursor: Any, plan: Mapping[str, Any], known: KnownColumns
) -> dict[tuple[str, str], str]:
    names = index_names(plan)
    lookup = _leading_lookup(names - known.keys())
    if lookup is not None:
        try:
            cursor.execute(lookup)
            rows = cursor.fetchall()
        except Exception:  # noqa: BLE001  # without it, rule 3 is simply not applied
            rows = None
        if rows is not None:
            found = {(str(r[0]), str(r[1])): str(r[2]) for r in rows}
            for name in names - known.keys():
                known[name] = found.get(name)
    return {name: col for name in names if (col := known.get(name)) is not None}


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


def run_explain(
    dbapi_connection: Any,
    sql: str,
    *,
    leading: bool = False,
    known: KnownColumns | None = None,
) -> Explained | ExplainError:
    """Plan ``sql`` on the test's own connection without changing its state.

    With ``leading``, the first key column of each index the plan uses with
    an index condition is read from pg_index inside the same savepoint,
    except for the indexes already in ``known``, which is updated.
    Database errors from EXPLAIN come back as ExplainError. An error while
    opening or rolling back the savepoint raises SavepointError: the
    connection's state is then unknown, and the caller stops checking.
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
        _manage(cursor, start)
        row: Any = None
        settings: Any = None
        error: ExplainError | None = None
        columns: dict[tuple[str, str], str] = {}
        try:
            cursor.execute(_SETTINGS)
            settings = cursor.fetchone()
            cursor.execute(_EXPLAIN + sql)
            row = cursor.fetchone()
        except Exception as exc:  # noqa: BLE001  # any database error from EXPLAIN
            error = ExplainError(_error_message(exc))
        plan = None if error else _read_plan(row[0] if row else None)
        if plan is not None and leading:
            columns = _read_leading(cursor, plan, {} if known is None else known)
        _manage(cursor, undo)
    finally:
        cursor.close()
    if error is not None:
        return error
    if plan is None:
        return ExplainError(f"unexpected EXPLAIN output: {row!r:.100}")
    schema = settings[-1] if settings else None
    return Explained(plan, schema if isinstance(schema, str) else None, columns)


def _manage(cursor: Any, statements: list[str]) -> None:
    for statement in statements:
        try:
            cursor.execute(statement)
        except Exception as exc:  # reported as a stopped check
            raise SavepointError(_error_message(exc)) from exc


# ---------------------------------------------------------------- listener

# Why the index check stopped for this session, after a broken savepoint.
_stopped: str | None = None


def swap_stopped(new: str | None) -> str | None:
    """Replace the stopped state and return the previous one (per session)."""
    global _stopped
    old, _stopped = _stopped, new
    return old


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
    global _stopped
    if executemany or not is_checked_statement(statement):
        return
    dialect = conn.dialect
    reason = _stopped or unsupported_reason(dialect)
    if reason is not None:
        for target in targets:
            target.index_notices[reason] = None
        return
    # Literals stay: a partial index may serve one value and not another.
    key = (id(conn.engine), collapse_lists(statement))
    fresh = [target for target in targets if key not in target.explained]
    if not fresh:
        return
    for target in fresh:
        target.explained.add(key)
    numbered = to_numbered(
        statement,
        dialect.paramstyle,
        unescape_quoted=getattr(dialect, "driver", None) != "pg8000",
    )
    assert numbered is not None  # unsupported_reason() checked the paramstyle
    version = getattr(dialect, "server_version_info", None)
    known = fresh[0].index_columns.setdefault(id(conn.engine), {})
    try:
        outcome = run_explain(
            conn.connection.dbapi_connection,
            numbered,
            leading=not version or version[0] < SKIP_SCAN_VERSION,
            known=known,
        )
    except SavepointError as exc:
        # The test's transaction may be broken now; N+1 detection and budgets
        # still work, but no more EXPLAIN runs on anyone's connection.
        _stopped = (
            "the index check stopped for the rest of the session: "
            f"it could not open or roll back its savepoint: {exc}"
        )
        for target in targets:
            target.index_notices[_stopped] = None
        return
    for target in fresh[1:]:
        target.index_columns.setdefault(id(conn.engine), {}).update(known)
    if isinstance(outcome, ExplainError):
        for target in fresh:
            target.explain_errors.append((outcome.message, statement))
        return
    issues = find_scans(outcome.plan, outcome.current_schema, outcome.leading)
    if not issues:
        return
    site = find_callsite()
    for issue in issues:
        scan = ScanEvent(issue, statement, site)
        for target in fresh:
            target.scans.append(scan)
