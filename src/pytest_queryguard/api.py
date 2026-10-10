from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager

from .collector import QueryCollector, pop, push
from .listeners import install
from .messages import format_budget


class QueryBudgetExceeded(AssertionError):
    """Raised when a block runs more SQL statements than its budget allows."""


@contextmanager
def _measure(limit: int) -> Iterator[QueryCollector]:
    # The budget failure is raised here; pytest should show the user's with block.
    __tracebackhide__ = True
    install()
    collector = QueryCollector()
    push(collector)
    try:
        yield collector
    finally:
        pop(collector)
    if len(collector.statements) > limit:
        raise QueryBudgetExceeded(format_budget(limit, collector.statements))


def max_queries(limit: int) -> AbstractContextManager[QueryCollector]:
    """Fail when the block runs more than ``limit`` SQL statements."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError(f"limit must be a non-negative integer, got {limit!r}")
    return _measure(limit)
