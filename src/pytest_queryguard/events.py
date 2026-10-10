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
