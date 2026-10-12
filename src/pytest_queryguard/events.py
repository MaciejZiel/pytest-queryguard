from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True, kw_only=True)
class CallSite:
    """A source location. Its fields are public API; its constructor is not."""

    filename: str
    lineno: int
    function: str


@dataclass(slots=True)
class StatementEvent:
    sql: str
    callsite: CallSite | None
    relationship: str | None
    # Seconds between the before and after cursor-execute events; None until
    # the statement has finished (or when it never did, because it raised).
    duration: float | None = None
    # True when the statement loaded columns of an instance that already
    # existed (a deferred column, or attributes expired by commit or expire()).
    column_load: bool = False
    # The bound parameters as the driver got them (not copied); None for
    # executemany, which can be large. Compared by the small-repeat rule.
    parameters: object = None
    # True when the driver ran the statement for a list of parameter sets.
    executemany: bool = False


@dataclass(frozen=True, slots=True)
class RelationshipLoadEvent:
    relationship: str
    parent_key: object
    callsite: CallSite | None
    scalar: bool = False  # many-to-one or one-to-one, not a collection


# What made the instance load columns again.
CAUSE_DEFERRED = "deferred"  # a deferred column, or one a query option left out
CAUSE_EXPIRED = "expired"  # expired by commit(), expire() or expire_all()
CAUSE_REFRESH = "refresh"  # session.refresh() without attribute names


@dataclass(slots=True)
class ColumnLoadEvent:
    """An instance that went back to the database for some of its columns."""

    owner: str  # the mapped class, "Child"
    attributes: tuple[str, ...]  # the attributes loaded, sorted
    cause: str  # one of the CAUSE_* values
    instance_key: object
    callsite: CallSite | None
    sql: str | None = None  # set when the statement runs


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
    fix: str | None = None  # a ready-made fix hint, for dialects that build their own


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
