from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CallSite:
    filename: str
    lineno: int
    function: str


@dataclass(frozen=True, slots=True)
class StatementEvent:
    sql: str
    callsite: CallSite | None
    relationship: str | None


@dataclass(frozen=True, slots=True)
class RelationshipLoadEvent:
    relationship: str
    parent_key: object
    callsite: CallSite | None


@dataclass(frozen=True, slots=True)
class ScanIssue:
    """A plan node that reads a whole table although the statement narrows it."""

    schema: str | None
    table: str
    node: str  # "Seq Scan" or "Index Scan using orders_pkey"
    kind: str  # "filter" or "join"
    condition: str
    column: str | None  # the column for a CREATE INDEX hint, when obvious


@dataclass(frozen=True, slots=True)
class ScanEvent:
    issue: ScanIssue
    sql: str
    callsite: CallSite | None
