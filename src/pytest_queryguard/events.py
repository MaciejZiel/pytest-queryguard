from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True, kw_only=True)
class CallSite:
    """A source location. Its fields are public API; its constructor is not."""

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
    kind: str  # "filter" or "index condition"
    condition: str
    column: str | None  # the column for a CREATE INDEX hint, when obvious
    show_schema: bool = True  # False when the table is in the current schema
    expression: bool = False  # the column sits in a function or a non-text cast


@dataclass(frozen=True, slots=True)
class ScanEvent:
    issue: ScanIssue
    sql: str
    callsite: CallSite | None


@dataclass(slots=True)
class Catalog:
    """What the index check read from the system catalogs, for one engine."""

    # (schema, index) -> first key column of a btree index, or None
    leading: dict[tuple[str, str], str | None] = field(default_factory=dict)
    # (schema, table) -> names of its varchar columns
    varchar: dict[tuple[str, str], frozenset[str]] = field(default_factory=dict)
