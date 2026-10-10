from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from .collector import QueryCollector
from .events import CallSite

_STRING = re.compile(r"'(?:[^']|'')*'")
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])")
_PLACEHOLDER = r"""\?|%s|%\(\w+\)s|:\w+|\$\d+|\d+(?:\.\d+)?|'(?:[^']|'')*'"""
_IN_LIST = re.compile(
    rf"\bIN\s*\(\s*(?:{_PLACEHOLDER})(?:\s*,\s*(?:{_PLACEHOLDER}))*\s*\)",
    re.IGNORECASE,
)
_DOLLAR_PARAM = re.compile(r"\$\d+")
_WS = re.compile(r"\s+")
_WRITES = ("INSERT", "UPDATE", "DELETE")


def normalize(sql: str) -> str:
    """Reduce a statement to a shape shared by all its executions."""
    out = _WS.sub(" ", sql).strip()
    # Lists first, so every placeholder style collapses to the same text.
    out = _IN_LIST.sub("IN (...)", out)
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


@dataclass(frozen=True, slots=True)
class RepeatFinding:
    sql: str
    count: int
    first: CallSite | None


Finding = RelationshipFinding | RepeatFinding


def _relationship_findings(
    collector: QueryCollector, threshold: int
) -> list[RelationshipFinding]:
    parents: dict[str, set[object]] = {}
    counts: Counter[str] = Counter()
    first: dict[str, CallSite | None] = {}
    for load in collector.relationship_loads:
        if load.parent_key is None:
            continue
        name = load.relationship
        parents.setdefault(name, set()).add(load.parent_key)
        counts[name] += 1
        if first.get(name) is None:
            first[name] = load.callsite
    samples: dict[str, str] = {}
    for stmt in collector.statements:
        if stmt.relationship is not None:
            samples.setdefault(stmt.relationship, stmt.sql)
    found = [
        RelationshipFinding(name, counts[name], first[name], samples.get(name))
        for name, keys in parents.items()
        if len(keys) >= threshold
    ]
    return sorted(found, key=lambda f: -f.count)


def _repeat_findings(collector: QueryCollector, threshold: int) -> list[RepeatFinding]:
    counts: Counter[str] = Counter()
    first: dict[str, CallSite | None] = {}
    for stmt in collector.statements:
        if stmt.relationship is not None:
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
    collector: QueryCollector, *, relationship_threshold: int, repeat_threshold: int
) -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(_relationship_findings(collector, relationship_threshold))
    findings.extend(_repeat_findings(collector, repeat_threshold))
    return findings
