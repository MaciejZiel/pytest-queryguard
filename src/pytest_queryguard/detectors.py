from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from .collector import QueryCollector
from .events import CAUSE_DEFERRED, CallSite, ColumnLoadEvent, StatementEvent

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


def collapse_lists(sql: str) -> str:
    """Collapse whitespace and parameter lists, so IN lists of any length match."""
    return _IN_LIST.sub("IN (...)", _WS.sub(" ", sql).strip())


def normalize(sql: str) -> str:
    """Reduce a statement to a shape shared by all its executions."""
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


@dataclass(frozen=True, slots=True)
class RepeatFinding:
    sql: str
    count: int
    first: CallSite | None
    # Found by the small-repeat rule: a few runs from one line, each with
    # different parameters, below the repeat threshold.
    varying: bool = False


@dataclass(frozen=True, slots=True)
class WriteLoopFinding:
    """One INSERT, UPDATE or DELETE shape run row by row from one line."""

    sql: str
    verb: str  # "INSERT", "UPDATE" or "DELETE"
    count: int
    first: CallSite | None


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

    @property
    def label(self) -> str:
        if self.cause == CAUSE_DEFERRED and self.attributes:
            return f"{self.owner}.{','.join(self.attributes)}"
        return f"{self.owner} ({self.cause})"


Detection = RelationshipFinding | RepeatFinding | ColumnLoadFinding | WriteLoopFinding
# The name this union had before findings.Finding existed.
Finding = Detection


def _relationship_findings(
    collector: QueryCollector, threshold: int
) -> list[RelationshipFinding]:
    parents: dict[str, set[object]] = {}
    counts: Counter[str] = Counter()
    first: dict[str, CallSite | None] = {}
    scalar: dict[str, bool] = {}
    for load in collector.relationship_loads:
        if load.parent_key is None:
            continue
        name = load.relationship
        parents.setdefault(name, set()).add(load.parent_key)
        counts[name] += 1
        if first.get(name) is None:
            first[name] = load.callsite
        scalar[name] = load.scalar
    samples: dict[str, str] = {}
    for stmt in collector.statements:
        if stmt.relationship is not None:
            samples.setdefault(stmt.relationship, stmt.sql)
    found = [
        RelationshipFinding(
            name, counts[name], first[name], samples.get(name), scalar[name]
        )
        for name, keys in parents.items()
        if len(keys) >= threshold
    ]
    return sorted(found, key=lambda f: -f.count)


def _column_load_findings(
    collector: QueryCollector, threshold: int
) -> list[ColumnLoadFinding]:
    groups: dict[tuple[str, tuple[str, ...], str], list[ColumnLoadEvent]] = {}
    for load in collector.column_loads:
        if load.cause != CAUSE_DEFERRED:
            attrs: tuple[str, ...] = ()
        else:
            attrs = load.attributes
        groups.setdefault((load.owner, attrs, load.cause), []).append(load)
    found: list[ColumnLoadFinding] = []
    for (owner, attrs, cause), loads in groups.items():
        keys = {load.instance_key for load in loads}
        if len(keys) < threshold:
            continue
        site = next((load.callsite for load in loads if load.callsite), None)
        sample = next((load.sql for load in loads if load.sql), None)
        found.append(
            ColumnLoadFinding(owner, attrs, cause, len(loads), len(keys), site, sample)
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
    collector: QueryCollector, threshold: int
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
    for (sql, _), stmts in groups.items():
        # All statements of one ORM flush count as one write: the unit of work
        # sends them one by one on a dialect without insertmanyvalues, and the
        # code can only call flush() less often, not batch them further.
        flushes = {s.flush for s in stmts if s.flush is not None}
        count = len(flushes) + sum(1 for s in stmts if s.flush is None)
        if count >= threshold:
            verb = stmts[0].sql.lstrip().split(None, 1)[0].upper()
            found.append(WriteLoopFinding(sql, verb, count, stmts[0].callsite))
    return sorted(found, key=lambda f: -f.count)


def _paginates(sql: str) -> bool:
    if _OFFSET.search(sql):
        return True
    return bool(_LIMIT.search(sql) and _RANGE.search(sql))


def _small_repeat_findings(
    collector: QueryCollector, small: int, threshold: int
) -> list[RepeatFinding]:
    """A few runs of one statement from one line, each with other parameters.

    Between ``small`` and ``threshold - 1`` runs: the repeat rule's threshold
    would not report them. Only a loop looks like this, so the rule asks for
    one call site and a different set of parameters (or literals) each time,
    and leaves pagination alone.
    """
    groups: dict[str, list[StatementEvent]] = {}
    for stmt in collector.statements:
        if stmt.relationship is not None or stmt.column_load:
            continue
        if stmt.sql.lstrip().upper().startswith(_WRITES) or _paginates(stmt.sql):
            continue
        groups.setdefault(normalize(stmt.sql), []).append(stmt)
    found: list[RepeatFinding] = []
    for key, stmts in groups.items():
        if not small <= len(stmts) < threshold:
            continue
        sites = {
            (s.callsite.filename, s.callsite.lineno) if s.callsite else None
            for s in stmts
        }
        if len(sites) != 1 or None in sites:
            continue
        runs = {(_WS.sub(" ", s.sql), repr(s.parameters)) for s in stmts}
        if len(runs) == len(stmts):
            found.append(RepeatFinding(key, len(stmts), stmts[0].callsite, True))
    return sorted(found, key=lambda f: -f.count)


def _repeat_findings(collector: QueryCollector, threshold: int) -> list[RepeatFinding]:
    counts: Counter[str] = Counter()
    first: dict[str, CallSite | None] = {}
    for stmt in collector.statements:
        if stmt.relationship is not None or stmt.column_load:
            continue
        if stmt.sql.lstrip().upper().startswith(_WRITES):
            continue
        key = normalize(stmt.sql)
        counts[key] += 1
        if first.get(key) is None:
            first[key] = stmt.callsite
    found = [
        RepeatFinding(sql, n, first[sql]) for sql, n in counts.items() if n >= threshold
    ]
    return sorted(found, key=lambda f: -f.count)


def detect(
    collector: QueryCollector,
    *,
    relationship_threshold: int,
    repeat_threshold: int,
    column_load_threshold: int = DEFAULT_COLUMN_LOAD_THRESHOLD,
    small_repeat_threshold: int | None = None,
    write_loop_threshold: int | None = None,
) -> list[Detection]:
    findings: list[Detection] = []
    findings.extend(_relationship_findings(collector, relationship_threshold))
    findings.extend(_column_load_findings(collector, column_load_threshold))
    findings.extend(_repeat_findings(collector, repeat_threshold))
    if small_repeat_threshold is not None and small_repeat_threshold < repeat_threshold:
        findings.extend(
            _small_repeat_findings(collector, small_repeat_threshold, repeat_threshold)
        )
    if write_loop_threshold is not None:
        findings.extend(_write_loop_findings(collector, write_loop_threshold))
    return findings
