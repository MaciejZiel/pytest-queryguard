from __future__ import annotations

import warnings
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import overload

from .collector import QueryCollector, pop, push
from .detectors import detect
from .events import CallSite, StatementEvent
from .listeners import install, internal_error
from .messages import display_root, format_budget, format_findings, make_findings
from .warnings import QueryGuardInternalWarning


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
        raise QueryBudgetExceeded(
            format_budget(limit, collector.statements, cwd=display_root())
        )


def max_queries(limit: int) -> AbstractContextManager[QueryCollector]:
    """Fail when the block runs more than ``limit`` SQL statements."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError(f"limit must be a non-negative integer, got {limit!r}")
    return _measure(limit)


@dataclass(frozen=True, slots=True, kw_only=True)
class Query:
    """One SQL statement recorded by ``record_queries()``.

    ``sql`` is the statement text as SQLAlchemy sent it to the driver.
    ``callsite`` is the first stack frame outside SQLAlchemy, pytest, the
    standard library, site-packages and this plugin, or None. When the
    statement loaded a relationship, lazily or as the extra query of
    ``selectinload``, ``relationship`` names it, as in ``"Order.customer"``;
    otherwise it is None.

    Not constructible by users: only ``record_queries()`` creates one. The
    fields are public; the constructor is not (it is keyword-only, and fields
    may be added in a minor release).
    """

    sql: str
    callsite: CallSite | None
    relationship: str | None


def _to_query(event: StatementEvent) -> Query:
    return Query(
        sql=event.sql, callsite=event.callsite, relationship=event.relationship
    )


class QueryLog:
    """The statements recorded by one ``record_queries()`` block.

    Inside the block it grows as statements run; after the block it no
    longer changes. It is sized, iterable and indexable (``len()``, ``for``,
    ``log[i]`` and ``log[a:b]``) but not a ``collections.abc.Sequence``: its
    ``count`` is a number, not the ``count(value)`` method of a sequence. An
    empty log is falsy, like any sized container.

    Not constructible by users: ``record_queries()`` is the only way to get
    one. Its constructor takes an internal type and may change in any release.
    """

    __slots__ = ("_collector", "_done", "_error", "_frozen")

    def __init__(self, collector: QueryCollector) -> None:
        # Not for direct use: only record_queries() creates a QueryLog.
        self._collector = collector
        # Set by _finish() when the block ends: an internal error during the
        # block, or the one that had already switched recording off.
        self._done = False
        self._error: BaseException | None = None
        self._frozen: tuple[Query, ...] = ()

    def _finish(self, earlier: BaseException | None) -> BaseException | None:
        self._error = internal_error() or earlier
        self._frozen = self._snapshot()  # built once; the log no longer changes
        self._done = True
        return self._error

    def _incomplete(self) -> BaseException | None:
        """The internal error that may have left this log incomplete, or None."""
        return self._error if self._done else internal_error()

    @property
    def queries(self) -> tuple[Query, ...]:
        """Every recorded statement, in the order it ran."""
        if self._done:
            return self._frozen
        return self._snapshot()

    def _snapshot(self) -> tuple[Query, ...]:
        return tuple(_to_query(event) for event in list(self._collector.statements))

    def __len__(self) -> int:
        return self.count

    def __iter__(self) -> Iterator[Query]:
        return iter(self.queries)

    @overload
    def __getitem__(self, index: int) -> Query: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[Query, ...]: ...

    def __getitem__(self, index: int | slice) -> Query | tuple[Query, ...]:
        """One recorded statement, or a tuple of them for a slice."""
        if self._done:
            return self._frozen[index]
        if isinstance(index, slice):  # live: convert only what is asked for
            return tuple(_to_query(e) for e in self._collector.statements[index])
        return _to_query(self._collector.statements[index])

    @property
    def count(self) -> int:
        """The number of recorded statements."""
        if self._done:  # the same tuple that queries and iteration use
            return len(self._frozen)
        return len(self._collector.statements)

    def assert_no_nplusone(
        self, *, relationship_threshold: int = 2, repeat_threshold: int = 5
    ) -> None:
        """Raise ``NPlusOneDetected`` when the recorded block has N+1 queries.

        The rules, and the defaults of the thresholds, are the plugin's: one
        relationship lazy-loaded for ``relationship_threshold`` different
        parent objects, or one ``SELECT``, without its literals and
        parameters, run ``repeat_threshold`` times.

        The thresholds are these two arguments only. The ``queryguard_*`` ini
        options, ``queryguard_mode`` and the ``allow_nplusone`` marker are not
        read, so a project that changes ``queryguard_repeat_threshold`` or
        ``queryguard_relationship_threshold`` must pass the same values here.

        Raises ``RuntimeError`` instead of checking when pytest-queryguard hit
        an internal error and stopped recording, because the log may then be
        incomplete (``record_queries()`` also warns with
        ``QueryGuardInternalWarning`` when the block ends).
        """
        __tracebackhide__ = True
        error = self._incomplete()
        if error is not None:
            raise RuntimeError(
                "the query log may be incomplete, so it cannot be checked: "
                f"pytest-queryguard stopped recording after an internal error: {error!r}"
            )
        for name, value in (
            ("relationship_threshold", relationship_threshold),
            ("repeat_threshold", repeat_threshold),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 2:
                raise ValueError(f"{name} must be an integer >= 2, got {value!r}")
        root = display_root()
        findings = make_findings(
            detect(
                self._collector,
                relationship_threshold=relationship_threshold,
                repeat_threshold=repeat_threshold,
            ),
            cwd=root,
        )
        if findings:
            raise NPlusOneDetected(format_findings(findings, cwd=root))

    def __repr__(self) -> str:
        return f"<QueryLog {self.count} queries>"


@contextmanager
def _record() -> Iterator[QueryLog]:
    install()
    collector = QueryCollector()
    log = QueryLog(collector)
    earlier = internal_error()  # recording is already off if this is set
    push(collector)
    try:
        yield log
    finally:
        pop(collector)
        # However the block ends, the log is fixed. The warning below is for a
        # normal exit only: it must not replace the exception that ended it.
        error = log._finish(earlier)
    if error is not None:
        warnings.warn(
            QueryGuardInternalWarning(
                "the QueryLog of this block may be incomplete: pytest-queryguard "
                f"stopped recording after an internal error: {error!r}"
            ),
            stacklevel=3,
        )


def record_queries() -> AbstractContextManager[QueryLog]:
    """Record every SQL statement SQLAlchemy sends inside the block.

    Statements from any thread count. The yielded ``QueryLog`` gives the
    statements, their number and an N+1 check.

    When the block ends normally after an internal error, it issues
    ``QueryGuardInternalWarning``. A block left by an exception does not warn,
    so the warning never replaces that exception; the log is fixed either way.
    """
    return _record()
