"""``assert_queries_do_not_grow()``: the statement count must not grow with N.

The same code runs once per size (a number of rows), each run in its own
``record_queries()`` block; the check fails when a larger size sends more
statements than the smallest one, and names the statements that grew.
"""

from __future__ import annotations

import inspect
import operator
from collections.abc import Awaitable, Callable, Iterable, Iterator
from contextlib import contextmanager
from typing import Any

from .api import QueryBudgetExceeded, QueryLog, record_queries
from .detectors import normalize
from .events import StatementEvent
from .messages import display_root, format_growth
from .sqltext import is_write

__all__ = ["assert_queries_do_not_grow", "assert_queries_do_not_grow_async"]


def _sizes(sizes: Iterable[int]) -> list[int]:
    """The sizes in ascending order, checked: at least two different integers >= 1.

    Not 0: code often returns early for no rows, and a constant query that it
    then skips would read as growth.
    """
    if isinstance(sizes, (str, bytes)) or not isinstance(sizes, Iterable):
        raise TypeError(f"sizes must be an iterable of integers, got {sizes!r}")
    values = []
    for value in sizes:
        if isinstance(value, bool):
            raise TypeError(f"each size must be an integer, got {value!r}")
        try:
            number = operator.index(value)  # int, or an integer type such as numpy's
        except TypeError:
            raise TypeError(f"each size must be an integer, got {value!r}") from None
        if number < 1:
            raise ValueError(f"each size must be 1 or more, got {value!r}")
        values.append(number)
    if len(set(values)) != len(values):
        raise ValueError(f"sizes must be different from each other, got {values!r}")
    if len(values) < 2:
        raise ValueError(f"sizes needs at least two sizes to compare, got {values!r}")
    return sorted(values)


def _check_callables(fn: object, setup: object) -> None:
    if not callable(fn):
        raise TypeError(f"fn must be callable, got {fn!r}")
    if setup is not None and not callable(setup):
        raise TypeError(f"setup must be None or callable, got {setup!r}")


@contextmanager
def _measured(logs: dict[int, QueryLog], size: int) -> Iterator[None]:
    with record_queries() as log:
        yield
    logs[size] = log


def _verdict(logs: dict[int, QueryLog]) -> None:
    """Raise when a larger size ran more statements than the smallest one."""
    __tracebackhide__ = True
    for log in logs.values():
        error = log._incomplete()
        if error is not None:
            raise RuntimeError(
                "the query log may be incomplete, so the counts cannot be "
                "compared: pytest-queryguard stopped recording after an "
                f"internal error: {error!r}"
            )
    # Each statement on its own, not only the total: a statement that runs
    # once per row must fail even when another one runs less often at a
    # larger size (a cache warmed by the first run).
    grown = _grown(logs)
    if not grown:
        return
    counts = {size: log.count for size, log in logs.items()}
    raise QueryBudgetExceeded(format_growth(counts, grown, cwd=display_root()))


def _grown(
    logs: dict[int, QueryLog],
) -> list[tuple[StatementEvent, dict[int, int], str]]:
    """The statements that ran more often at a larger size than at the smallest.

    A statement is its shape (``normalize``: without literals and parameters),
    the line it ran from and the relationship it loaded. Each comes with its
    count per size, and the kind of fix: ``relationship`` (a lazy load),
    ``write`` or ``repeat``. In the order they first ran at the largest size
    where they ran.
    """
    sizes = list(logs)
    groups: dict[tuple[object, ...], tuple[StatementEvent, dict[int, int]]] = {}
    for size in reversed(sizes):  # the largest size first: its order is shown
        log = logs[size]
        # the statements the frozen count covers, as the budget failure does
        for event in log._collector.statements[: log.count]:
            site = event.callsite
            key = (
                normalize(event.sql),
                None if site is None else (site.filename, site.lineno),
                event.relationship,
            )
            group = groups.get(key)
            if group is None:
                group = groups[key] = (event, dict.fromkeys(sizes, 0))
            group[1][size] += 1
    grown = []
    for event, counts in groups.values():
        if any(counts[size] > counts[sizes[0]] for size in sizes[1:]):
            if event.relationship is not None and event.lazy_load:
                fix = "relationship"
            elif is_write(event.sql):
                fix = "write"
            else:
                fix = "repeat"
            grown.append((event, counts, fix))
    return grown


def assert_queries_do_not_grow(
    fn: Callable[[Any], object],
    sizes: Iterable[int] = (1, 10),
    *,
    setup: Callable[[int], object] | None = None,
) -> dict[int, QueryLog]:
    """Fail when ``fn`` sends more SQL statements for a larger size.

    For each size, in ascending order, ``setup(size)`` runs first, unrecorded
    (create that many rows there), and then ``fn`` with what ``setup``
    returned, inside a ``record_queries()`` block. Without ``setup``, ``fn``
    gets the size. ``sizes`` needs at least two different integers >= 1. A
    generator, iterator or Django ``QuerySet`` that ``fn`` returns is iterated
    inside the block, so its queries count.

    Raises ``QueryBudgetExceeded`` when a statement (its shape, line and
    relationship) runs more often at a larger size than at the smallest one,
    with the count per size and each statement that grew: its line, its count
    per size and a fix. Returns the ``QueryLog`` of each size, keyed by size,
    in ascending order.

    Statements from any thread count, as in ``record_queries()``. For a
    coroutine function, use ``assert_queries_do_not_grow_async()``.
    """
    __tracebackhide__ = True
    order = _sizes(sizes)
    _check_callables(fn, setup)
    logs: dict[int, QueryLog] = {}
    for size in order:
        arg = size if setup is None else setup(size)
        if inspect.isawaitable(arg):
            _close(arg)
            raise TypeError(
                "setup returned an awaitable: use assert_queries_do_not_grow_async()"
            )
        with _measured(logs, size):
            result = fn(arg)
            if inspect.isawaitable(result):
                _close(result)
                raise TypeError(
                    "fn returned an awaitable: use assert_queries_do_not_grow_async()"
                )
            _consume(result)
    _verdict(logs)
    return logs


async def assert_queries_do_not_grow_async(
    fn: Callable[[Any], object],
    sizes: Iterable[int] = (1, 10),
    *,
    setup: Callable[[int], object] | None = None,
) -> dict[int, QueryLog]:
    """``assert_queries_do_not_grow()`` for async code: await it.

    ``fn`` and ``setup`` may be coroutine functions or plain functions; what
    they return is awaited when it is awaitable.
    """
    __tracebackhide__ = True
    order = _sizes(sizes)
    _check_callables(fn, setup)
    logs: dict[int, QueryLog] = {}
    for size in order:
        arg = size if setup is None else await _resolve(setup(size))
        with _measured(logs, size):
            _consume(await _resolve(fn(arg)))
    _verdict(logs)
    return logs


def _consume(result: object) -> None:
    """Run the queries of a lazy result inside the recording.

    A generator or other iterator, or a Django ``QuerySet``, sends its SQL
    only when it is iterated; returned as is, the block would record nothing.
    """
    if isinstance(result, Iterator) or callable(getattr(result, "_fetch_all", None)):
        for _ in result:  # type: ignore[attr-defined]
            pass


async def _resolve(value: object) -> object:
    if inspect.isawaitable(value):
        return await value
    return value


def _close(value: Awaitable[object]) -> None:
    """Close a coroutine that will not be awaited, without a RuntimeWarning."""
    close = getattr(value, "close", None)
    if callable(close):
        close()
