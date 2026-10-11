from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path

from .collector import QueryCollector, pop, push
from .detectors import detect
from .events import CallSite
from .listeners import install
from .messages import format_budget, format_findings


class QueryBudgetExceeded(AssertionError):
    """Raised when a block runs more SQL statements than its budget allows."""


class NPlusOneDetected(AssertionError):
    """Raised by ``QueryLog.assert_no_nplusone()`` when it finds N+1 queries."""


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


@dataclass(frozen=True, slots=True)
class Query:
    """One SQL statement recorded by ``record_queries()``.

    ``sql`` is the statement text as SQLAlchemy sent it to the driver.
    ``callsite`` is the first stack frame outside SQLAlchemy, pytest, the
    standard library, site-packages and this plugin, or None. When the
    statement loaded a relationship, lazily or as the extra query of
    ``selectinload``, ``relationship`` names it, as in ``"Order.customer"``;
    otherwise it is None.
    """

    sql: str
    callsite: CallSite | None
    relationship: str | None


class QueryLog:
    """The statements recorded by one ``record_queries()`` block.

    Inside the block it grows as statements run; after the block it no
    longer changes.
    """

    __slots__ = ("_collector",)

    def __init__(self, collector: QueryCollector) -> None:
        self._collector = collector

    @property
    def queries(self) -> tuple[Query, ...]:
        """Every recorded statement, in the order it ran."""
        return tuple(
            Query(event.sql, event.callsite, event.relationship)
            for event in list(self._collector.statements)
        )

    @property
    def count(self) -> int:
        """The number of recorded statements."""
        return len(self._collector.statements)

    def assert_no_nplusone(
        self, *, relationship_threshold: int = 2, repeat_threshold: int = 5
    ) -> None:
        """Raise ``NPlusOneDetected`` when the recorded block has N+1 queries.

        The rules, and the defaults of the thresholds, are the plugin's: one
        relationship lazy-loaded for ``relationship_threshold`` different
        parent objects, or one ``SELECT``, without its literals and
        parameters, run ``repeat_threshold`` times.
        """
        __tracebackhide__ = True
        for name, value in (
            ("relationship_threshold", relationship_threshold),
            ("repeat_threshold", repeat_threshold),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 2:
                raise ValueError(f"{name} must be an integer >= 2, got {value!r}")
        findings = detect(
            self._collector,
            relationship_threshold=relationship_threshold,
            repeat_threshold=repeat_threshold,
        )
        if findings:
            raise NPlusOneDetected(format_findings(findings, cwd=Path.cwd()))

    def __repr__(self) -> str:
        return f"<QueryLog {self.count} queries>"


@contextmanager
def _record() -> Iterator[QueryLog]:
    install()
    collector = QueryCollector()
    push(collector)
    try:
        yield QueryLog(collector)
    finally:
        pop(collector)


def record_queries() -> AbstractContextManager[QueryLog]:
    """Record every SQL statement SQLAlchemy sends inside the block.

    Statements from any thread count. The yielded ``QueryLog`` gives the
    statements, their number and an N+1 check.
    """
    return _record()
