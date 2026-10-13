from __future__ import annotations

import functools
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar

from .collector import QueryCollector
from .events import (
    CAUSE_DEFERRED,
    CallSite,
    ColumnLoadEvent,
    RelationshipLoadEvent,
    StatementEvent,
)

_STRING = re.compile(r"'(?:[^']|'')*'")
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])")
_PLACEHOLDER = r"""\?|%s|%\(\w+\)s|:\w+|\$\d+|\d+(?:\.\d+)?|'(?:[^']|'')*'"""
# A type name as drivers render it: INTEGER, DOUBLE PRECISION, VARCHAR(20)[],
# TIMESTAMP(3) WITH TIME ZONE, public.mood, "Sales"."Mood".
_NAME = r'(?:"(?:[^"]|"")+"|[A-Za-z_]\w*)'
_WORD = r"\s+[A-Za-z_]\w*"
_TYPE = (
    rf"{_NAME}(?:\s*\.\s*{_NAME})*(?:{_WORD})*"
    rf"(?:\s*\(\s*\d+(?:\s*,\s*\d+)*\s*\)(?:{_WORD})*)?"
    r"(?:\s*\[\s*\d*\s*\])*"
)
# PostgreSQL drivers may cast each bound value: $1::INTEGER or CAST(? AS INTEGER).
_ITEM = (
    rf"(?:CAST\s*\(\s*(?:{_PLACEHOLDER})\s+AS\s+{_TYPE}\s*\)"
    rf"|(?:{_PLACEHOLDER})(?:\s*::\s*{_TYPE})?)"
)
_IN_LIST = re.compile(
    rf"\bIN\s*\(\s*{_ITEM}(?:\s*,\s*{_ITEM})*\s*\)",
    re.IGNORECASE,
)
_DOLLAR_PARAM = re.compile(r"\$\d+")
_WS = re.compile(r"\s+")
_WRITES = ("INSERT", "UPDATE", "DELETE")
# Pagination: OFFSET, or LIMIT with a range comparison on a parameter (keyset).
_OFFSET = re.compile(r"\bOFFSET\b", re.IGNORECASE)
_LIMIT = re.compile(r"\bLIMIT\b|\bFETCH\s+(?:FIRST|NEXT)\b|\bTOP\b", re.IGNORECASE)
_RANGE = re.compile(r"[<>]=?\s*(?:\?|%s|%\(\w+\)s|:\w+|\$\d+)")
# Distinct instances that must load columns on their own to count as N+1: as
# high as the repeat rule, because touching a few objects after a commit is
# ordinary test code.
DEFAULT_COLUMN_LOAD_THRESHOLD = 5
_T = TypeVar("_T")

# How the count rules group what they count: one group per test, or one per
# Session (or connection checkout). See docs/how-detection-works.md.
SCOPE_TEST = "test"
SCOPE_SESSION = "session"
SCOPES = (SCOPE_TEST, SCOPE_SESSION)


def collapse_lists(sql: str) -> str:
    """Collapse whitespace and parameter lists, so IN lists of any length match."""
    return _IN_LIST.sub("IN (...)", _WS.sub(" ", sql).strip())


@functools.lru_cache(maxsize=2048)
def normalize(sql: str) -> str:
    """Reduce a statement to a shape shared by all its executions.

    Pure, and a suite repeats the same statement texts, so the last 2048 are
    remembered.
    """
    # Lists first, so every placeholder style collapses to the same text.
    out = collapse_lists(sql)
    out = _STRING.sub("?", out)
    # Numbered parameters ($1, $2) become "?" so they compare equal.
    out = _DOLLAR_PARAM.sub("?", out)
    return _NUMBER.sub("?", out)


@dataclass(frozen=True, slots=True)
class RelationshipFinding:
    relationship: str
    count: int
    first: CallSite | None
    sample_sql: str | None
    scalar: bool = False  # many-to-one or one-to-one: joinedload, not selectinload
    # With scope "test": how many Sessions the loads came from. With scope
    # "session" it stays 1 and ``scoped`` is set: the count is for one Session.
    scopes: int = 1
    scoped: bool = False


@dataclass(frozen=True, slots=True)
class RepeatFinding:
    sql: str
    count: int
    first: CallSite | None
    # Found by the small-repeat rule: a few runs from one line, each with
    # different parameters, below the repeat threshold.
    varying: bool = False
    # See RelationshipFinding: Sessions or connection checkouts counted over.
    scopes: int = 1
    scoped: bool = False


@dataclass(frozen=True, slots=True)
class WriteLoopFinding:
    """One INSERT, UPDATE or DELETE shape run row by row from one line."""

    sql: str
    verb: str  # "INSERT", "UPDATE" or "DELETE"
    count: int
    first: CallSite | None
    scopes: int = 1
    scoped: bool = False


@dataclass(frozen=True, slots=True)
class ColumnLoadFinding:
    """Instances of one class that each loaded columns in their own statement."""

    owner: str  # the mapped class
    attributes: tuple[str, ...]  # what was loaded (deferred columns only)
    cause: str  # events.CAUSE_*
    count: int  # loads
    instances: int  # distinct instances
    first: CallSite | None
    sample_sql: str | None
    scopes: int = 1
    scoped: bool = False

    @property
    def label(self) -> str:
        if self.cause == CAUSE_DEFERRED and self.attributes:
            return f"{self.owner}.{','.join(self.attributes)}"
        return f"{self.owner} ({self.cause})"


Detection = RelationshipFinding | RepeatFinding | ColumnLoadFinding | WriteLoopFinding
# The name this union had before findings.Finding existed.
Finding = Detection


def _split(
    items: Sequence[_T], scope: Callable[[_T], object], scoped: bool
) -> list[list[_T]]:
    """One group with everything, or one group per scope (in order of first use)."""
    if not scoped:
        return [list(items)]
    groups: dict[object, list[_T]] = {}
    for item in items:
        groups.setdefault(scope(item), []).append(item)
    return list(groups.values())


def _scope_count(items: Sequence[_T], scope: Callable[[_T], object]) -> int:
    return len({scope(item) for item in items})


def _relationship_findings(
    collector: QueryCollector, threshold: int, scoped: bool = False
) -> list[RelationshipFinding]:
    by_name: dict[str, list[RelationshipLoadEvent]] = {}
    for load in collector.relationship_loads:
        if load.parent_key is not None:
            by_name.setdefault(load.relationship, []).append(load)
    found: list[RelationshipFinding] = []
    for name, loads in by_name.items():
        best: list[RelationshipLoadEvent] | None = None
        best_parents = 0
        for group in _split(loads, lambda load: load.scope, scoped):
            parents = len({load.parent_key for load in group})
            if parents >= threshold and parents > best_parents:
                best, best_parents = group, parents
        if best is None:
            continue
        scope = best[0].scope
        sample = None
        for stmt in collector.statements:
            if stmt.relationship != name:
                continue
            if sample is None:
                sample = stmt.sql
            if not scoped or stmt.scope == scope:
                sample = stmt.sql
                break
        site = next((load.callsite for load in best if load.callsite), None)
        found.append(
            RelationshipFinding(
                name,
                len(best),
                site,
                sample,
                best[-1].scalar,
                1 if scoped else _scope_count(best, lambda load: load.scope),
                scoped,
            )
        )
    return sorted(found, key=lambda f: -f.count)


def _column_load_findings(
    collector: QueryCollector, threshold: int, scoped: bool = False
) -> list[ColumnLoadFinding]:
    groups: dict[tuple[str, tuple[str, ...], str], list[ColumnLoadEvent]] = {}
    for load in collector.column_loads:
        if load.cause != CAUSE_DEFERRED:
            attrs: tuple[str, ...] = ()
        else:
            attrs = load.attributes
        groups.setdefault((load.owner, attrs, load.cause), []).append(load)
    found: list[ColumnLoadFinding] = []
    for (owner, attrs, cause), all_loads in groups.items():
        best: list[ColumnLoadEvent] | None = None
        best_keys = 0
        for loads in _split(all_loads, lambda load: load.scope, scoped):
            keys = len({load.instance_key for load in loads})
            if keys >= threshold and keys > best_keys:
                best, best_keys = loads, keys
        if best is None:
            continue
        site = next((load.callsite for load in best if load.callsite), None)
        sample = next((load.sql for load in best if load.sql), None)
        found.append(
            ColumnLoadFinding(
                owner,
                attrs,
                cause,
                len(best),
                best_keys,
                site,
                sample,
                1 if scoped else _scope_count(best, lambda load: load.scope),
                scoped,
            )
        )
    return sorted(found, key=lambda f: -f.count)


def _value_rows(sql: str) -> int:
    """The number of row tuples in the VALUES list of a statement (1 without one)."""
    match = re.search(r"\bVALUES\b", sql, re.IGNORECASE)
    if match is None:
        return 1
    rows, depth, i, n = 0, 0, match.end(), len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":  # a string literal; '' inside it is an escaped quote
            end = i + 1
            while end < n:
                if sql[end] == "'" and sql[end + 1 : end + 2] == "'":
                    end += 2
                elif sql[end] == "'":
                    break
                else:
                    end += 1
            i = end
        elif ch == "(":
            if depth == 0:
                rows += 1
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and ch not in " ,\n\t":
            break  # RETURNING, ON CONFLICT ...: the list is over
        i += 1
    return max(rows, 1)


def _write_loop_findings(
    collector: QueryCollector, threshold: int, scoped: bool = False
) -> list[WriteLoopFinding]:
    """The same write, one row per statement, from one line.

    ``executemany`` and multi-row ``VALUES`` statements are already batched,
    so they are never counted, and the statements of one ORM flush count as
    one: SQLAlchemy sent them as it had to.
    """
    groups: dict[tuple[str, object], list[StatementEvent]] = {}
    for stmt in collector.statements:
        if stmt.executemany or not stmt.sql.lstrip().upper().startswith(_WRITES):
            continue
        if _value_rows(_WS.sub(" ", stmt.sql)) > 1:
            continue
        site = stmt.callsite
        where = None if site is None else (site.filename, site.lineno)
        groups.setdefault((normalize(stmt.sql), where), []).append(stmt)
    found = []
    for (sql, _), all_stmts in groups.items():
        best: list[StatementEvent] | None = None
        best_count = 0
        for stmts in _split(all_stmts, lambda s: s.scope, scoped):
            # All statements of one ORM flush count as one write: the unit of
            # work sends them one by one on a dialect without insertmanyvalues,
            # and the code can only call flush() less often, not batch them
            # further.
            flushes = {s.flush for s in stmts if s.flush is not None}
            count = len(flushes) + sum(1 for s in stmts if s.flush is None)
            if count >= threshold and count > best_count:
                best, best_count = stmts, count
        if best is None:
            continue
        verb = best[0].sql.lstrip().split(None, 1)[0].upper()
        found.append(
            WriteLoopFinding(
                sql,
                verb,
                best_count,
                best[0].callsite,
                1 if scoped else _scope_count(best, lambda s: s.scope),
                scoped,
            )
        )
    return sorted(found, key=lambda f: -f.count)


# Statements the database's own catalogs answer, which SQLAlchemy and Alembic
# run for schema reflection. The application cannot batch them, so the repeat
# rules do not count them (budgets and the report still do):
#   - a PRAGMA (SQLite: table_info, index_list, foreign_key_list, ...);
#   - a read of a SQLite catalog table: sqlite_master, sqlite_schema,
#     sqlite_temp_master or sqlite_temp_schema, with or without main./temp.;
#   - a read of anything in the pg_catalog or information_schema schema
#     (PostgreSQL; information_schema also exists in MySQL and MariaDB).
# Matched on the normalized text, where string literals are already gone, so a
# value 'sqlite_master' in a WHERE clause does not hide a statement. A
# statement that joins an application table with a catalog is excluded too.
# Not excluded: MySQL's SHOW and DESCRIBE, and PostgreSQL catalog tables read
# without the pg_catalog schema (pg_class on the search path).
_CATALOG = re.compile(
    r"""
      \A[\s(]*PRAGMA\b
    | (?<![\w$.])(?:"?(?:main|temp)"?\s*\.\s*)?"?sqlite_(?:temp_)?(?:master|schema)"?(?![\w$])
    | (?<![\w$.])"?(?:pg_catalog|information_schema)"?\s*\.
    """,
    re.IGNORECASE | re.VERBOSE,
)


def is_catalog_statement(sql: str) -> bool:
    """True for schema reflection that the repeat rules do not count."""
    return _CATALOG.search(sql) is not None


def _paginates(sql: str) -> bool:
    if _OFFSET.search(sql):
        return True
    return bool(_LIMIT.search(sql) and _RANGE.search(sql))


def _repeat_groups(collector: QueryCollector) -> dict[str, list[StatementEvent]]:
    """The statements the repeat rules count, by normalized text."""
    groups: dict[str, list[StatementEvent]] = {}
    for stmt in collector.statements:
        if stmt.relationship is not None or stmt.column_load:
            continue
        if stmt.sql.lstrip().upper().startswith(_WRITES):
            continue
        groups.setdefault(normalize(stmt.sql), []).append(stmt)
    return {key: g for key, g in groups.items() if not is_catalog_statement(key)}


def _small_repeat_findings(
    collector: QueryCollector,
    small: int,
    threshold: int,
    scoped: bool = False,
    skip: frozenset[str] = frozenset(),
) -> list[RepeatFinding]:
    """A few runs of one statement from one line, each with other parameters.

    Between ``small`` and ``threshold - 1`` runs: the repeat rule's threshold
    would not report them. Only a loop looks like this, so the rule asks for
    one call site and a different set of parameters (or literals) each time,
    and leaves pagination alone.
    """
    found: list[RepeatFinding] = []
    for key, all_stmts in _repeat_groups(collector).items():
        if key in skip or _paginates(all_stmts[0].sql):
            continue
        best: list[StatementEvent] | None = None
        for stmts in _split(all_stmts, lambda s: s.scope, scoped):
            if not small <= len(stmts) < threshold:
                continue
            sites = {
                (s.callsite.filename, s.callsite.lineno) if s.callsite else None
                for s in stmts
            }
            if len(sites) != 1 or None in sites:
                continue
            runs = {(_WS.sub(" ", s.sql), repr(s.parameters)) for s in stmts}
            if len(runs) == len(stmts) and (best is None or len(stmts) > len(best)):
                best = stmts
        if best is not None:
            found.append(
                RepeatFinding(
                    key,
                    len(best),
                    best[0].callsite,
                    True,
                    1 if scoped else _scope_count(best, lambda s: s.scope),
                    scoped,
                )
            )
    return sorted(found, key=lambda f: -f.count)


def _repeat_findings(
    collector: QueryCollector, threshold: int, scoped: bool = False
) -> list[RepeatFinding]:
    found: list[RepeatFinding] = []
    for key, all_stmts in _repeat_groups(collector).items():
        best: list[StatementEvent] | None = None
        for stmts in _split(all_stmts, lambda s: s.scope, scoped):
            if len(stmts) >= threshold and (best is None or len(stmts) > len(best)):
                best = stmts
        if best is None:
            continue
        site = next((s.callsite for s in best if s.callsite), None)
        found.append(
            RepeatFinding(
                key,
                len(best),
                site,
                False,
                1 if scoped else _scope_count(best, lambda s: s.scope),
                scoped,
            )
        )
    return sorted(found, key=lambda f: -f.count)


def detect(
    collector: QueryCollector,
    *,
    relationship_threshold: int,
    repeat_threshold: int,
    column_load_threshold: int = DEFAULT_COLUMN_LOAD_THRESHOLD,
    small_repeat_threshold: int | None = None,
    write_loop_threshold: int | None = None,
    scope: str = SCOPE_TEST,
) -> list[Detection]:
    """Run the count rules; ``scope`` is "test" (all) or "session" (per Session)."""
    scoped = scope == SCOPE_SESSION
    findings: list[Detection] = []
    findings.extend(_relationship_findings(collector, relationship_threshold, scoped))
    findings.extend(_column_load_findings(collector, column_load_threshold, scoped))
    repeats = _repeat_findings(collector, repeat_threshold, scoped)
    findings.extend(repeats)
    if small_repeat_threshold is not None and small_repeat_threshold < repeat_threshold:
        findings.extend(
            _small_repeat_findings(
                collector,
                small_repeat_threshold,
                repeat_threshold,
                scoped,
                frozenset(f.sql for f in repeats),
            )
        )
    if write_loop_threshold is not None:
        findings.extend(_write_loop_findings(collector, write_loop_threshold, scoped))
    return findings
