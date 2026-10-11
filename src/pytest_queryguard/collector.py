from __future__ import annotations

from dataclasses import dataclass, field

from .events import RelationshipLoadEvent, ScanEvent, StatementEvent


@dataclass(slots=True, eq=False)
class QueryCollector:
    statements: list[StatementEvent] = field(default_factory=list)
    relationship_loads: list[RelationshipLoadEvent] = field(default_factory=list)
    # Missing-index check (see indexes.py); only used when index_check is set.
    index_check: bool = False
    explained: set[object] = field(default_factory=set)
    scans: list[ScanEvent] = field(default_factory=list)
    index_notices: dict[str, None] = field(default_factory=dict)  # ordered set
    explain_errors: list[tuple[str, str]] = field(default_factory=list)
    # per engine: (schema, index) -> first key column, see indexes.py
    index_columns: dict[int, dict[tuple[str, str], str | None]] = field(
        default_factory=dict
    )


# Process-wide on purpose: the code under test may run in another thread.
_stack: list[QueryCollector] = []
_generation = 0


def generation() -> int:
    """Changes on every push/pop; lets per-measurement state detect staleness."""
    return _generation


def push(collector: QueryCollector) -> None:
    global _generation
    _generation += 1
    _stack.append(collector)


def pop(collector: QueryCollector) -> None:
    global _generation
    _generation += 1
    for i in range(len(_stack) - 1, -1, -1):
        if _stack[i] is collector:
            del _stack[i]
            break


def active() -> tuple[QueryCollector, ...]:
    return tuple(_stack)


def _reset_for_tests() -> None:
    global _generation
    _generation += 1
    _stack.clear()
