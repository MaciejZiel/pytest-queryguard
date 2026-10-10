from __future__ import annotations

from dataclasses import dataclass, field

from .events import RelationshipLoadEvent, StatementEvent


@dataclass(slots=True)
class QueryCollector:
    statements: list[StatementEvent] = field(default_factory=list)
    relationship_loads: list[RelationshipLoadEvent] = field(default_factory=list)


# Process-wide on purpose: the code under test may run in another thread.
_stack: list[QueryCollector] = []


def push(collector: QueryCollector) -> None:
    _stack.append(collector)


def pop(collector: QueryCollector) -> None:
    if collector in _stack:
        _stack.remove(collector)


def active() -> tuple[QueryCollector, ...]:
    return tuple(_stack)


def _reset_for_tests() -> None:
    _stack.clear()
