"""Experimental: Django querysets loaded only to count them or test them.

Nothing here runs unless ``queryguard_queryset_len`` is ``warn`` or
``fail``: ``enable()`` turns it on, and the Django backend's patches of
``QuerySet`` pass straight through while no tracker is current.

How it works (see docs/django.md, section queryset-len):

* ``len(qs)`` or ``bool(qs)`` (``if qs:``, ``not qs``) on a queryset that has
  not run yet runs its full ``SELECT`` and keeps every row, where
  ``qs.count()`` or ``qs.exists()`` would run a ``COUNT`` or a ``LIMIT 1``.
  The backend's ``QuerySet.__len__`` and ``__bool__`` call ``begin()`` and
  ``finish()`` around such an evaluation, which note it here with the line
  that made it and the statement it ran.
* Every way of reading the rows afterwards (``__iter__``, ``__aiter__``,
  ``__getitem__``, ``contains()`` and pickling) calls ``used()``, which
  clears the note. ``list(qs)`` calls ``len()`` and then iterates.

A note becomes a finding at the end of the test only when nothing doubts
it: the call was made by the application (not by Django or another library,
which may read the result cache directly, and not by test code); the
queryset has no slice (``LIMIT``); a statement ran; no row was read; the
queryset is gone when the test ends (one still referenced may be read
later); and it was not handed in or out of the function that counted it
(an argument, a variable the function reads more than once, a closure
variable). Prefer a missed case to a false one.
"""

from __future__ import annotations

import dis
import inspect
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from types import CodeType, FrameType
from typing import Any

from . import blocks, callsite
from .blocks import IgnoreBlock
from .events import CallSite
from .findings import KIND_QUERYSET_LEN, Finding
from .warnings import QueryGuardInternalWarning, warn_internal

HOW_LEN = "len"
HOW_BOOL = "bool"


@dataclass(slots=True, eq=False)
class Evaluation:
    """One queryset that ``len()`` or ``bool()`` made run."""

    how: str  # HOW_LEN or HOW_BOOL
    model: str
    callsite: CallSite | None
    queryset: weakref.ref[Any]
    # the ignore() blocks active where it ran (see blocks.py)
    ignores: tuple[IgnoreBlock, ...] = ()
    # handed in or out of the function that counted it: may be read later
    escapes: bool = False
    sql: str | None = None  # the first SELECT it ran, set by the backend
    rows: int | None = None  # for len(): how many rows it loaded
    used: bool = False

    @property
    def label(self) -> str:
        return f"{self.how}({self.model})"


@dataclass(slots=True, eq=False)
class Tracker:
    """The evaluations of one test body whose rows nothing has read yet."""

    # by id() of the evaluation, in order; one whose rows are read is dropped
    evaluations: dict[int, Evaluation] = field(default_factory=dict)
    # id() of the queryset -> its evaluation, while no row has been read
    open: dict[int, Evaluation] = field(default_factory=dict)
    previous: Tracker | None = None


# The tracker of the test body measured now; read by the Django backend's
# patches on every call, so a plain module attribute.
current: Tracker | None = None
_enabled = 0
_broken = False
_warned = False
# (id of a code object, name) -> (the code object, how many instructions
# read that local). By id(): a code object hashes its bytecode; it is kept
# with the answer, so its id is not reused while the entry is there.
_read_counts: dict[tuple[int, str], tuple[CodeType, int]] = {}
_MAX_READ_COUNTS = 4096


def _broke(exc: Exception, where: str) -> None:
    """Turn the check off for the rest of the process and say so, once."""
    global _broken, _warned, current
    _broken = True
    current = None
    if not _warned:
        _warned = True
        warn_internal(
            QueryGuardInternalWarning(
                "pytest-queryguard disabled the queryset-len check after an "
                f"internal error in {where}: {exc!r}"
            )
        )


def enable() -> None:
    """Switch the check on; calls nest, see ``disable()``."""
    global _enabled
    _enabled += 1


def disable() -> None:
    """Undo one ``enable()``."""
    global _enabled, current
    if _enabled == 0:
        return
    _enabled -= 1
    if _enabled == 0:
        current = None


def is_enabled() -> bool:
    return _enabled > 0 and not _broken


def start() -> Tracker:
    """Begin tracking the test body; pair with ``stop()``."""
    global current
    tracker = Tracker(previous=current)
    current = tracker
    return tracker


def stop(tracker: Tracker) -> None:
    global current
    current = tracker.previous if not _broken else None
    tracker.open.clear()


# --- called by the Django backend ---------------------------------------------


def begin(queryset: Any, how: str, caller: FrameType | None) -> Evaluation | None:
    """Note that ``len()`` or ``bool()`` is about to run ``queryset``.

    ``caller`` is the frame that called ``__len__``/``__bool__``. None when
    the evaluation is not one to note; never raises.
    """
    try:
        if caller is None or callsite._is_internal(caller.f_code.co_filename):
            return None  # Django, a library or a template: they read the cache
        query = getattr(queryset, "query", None)
        if query is None or getattr(query, "is_sliced", False):
            return None
        site, origin = callsite.find_site_origin()
        if origin.test_code:
            return None  # an assertion of the test, not the application
        model = getattr(queryset, "model", None)
        return Evaluation(
            how=how,
            model=getattr(model, "__name__", None) or type(queryset).__name__,
            callsite=site,
            queryset=weakref.ref(queryset),
            ignores=blocks.current(),
            escapes=_handed_over(queryset, caller),
        )
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        _broke(exc, "the queryset-len check (evaluation)")
        return None


def finish(evaluation: Evaluation, queryset: Any, result: object) -> None:
    """The evaluation ran: track it until its rows are read; never raises."""
    tracker = current
    if tracker is None:
        return
    try:
        if evaluation.how == HOW_LEN and isinstance(result, int):
            evaluation.rows = result
        tracker.evaluations[id(evaluation)] = evaluation
        tracker.open[id(queryset)] = evaluation
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        _broke(exc, "the queryset-len check (evaluation)")


def used(tracker: Tracker, queryset: Any) -> None:
    """The rows of ``queryset`` are being read; never raises."""
    try:
        evaluation = tracker.open.pop(id(queryset), None)
        if evaluation is not None and evaluation.queryset() is queryset:
            evaluation.used = True
            tracker.evaluations.pop(id(evaluation), None)
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        _broke(exc, "the queryset-len check (read)")


def counted(tracker: Tracker, queryset: Any) -> None:
    """``len()`` or ``count()`` of a queryset that ``bool()`` ran: the rows
    were loaded to count them, so ``count()`` alone is the fix; never raises."""
    try:
        evaluation = tracker.open.get(id(queryset))
        if evaluation is not None and evaluation.queryset() is queryset:
            evaluation.how = HOW_LEN
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        _broke(exc, "the queryset-len check (count)")


# --- whether the queryset may be read where the test cannot see it -------------

# Instructions that read a local variable; LOAD_FAST_AND_CLEAR is left out:
# it saves the outer variable around an inlined comprehension (3.12+).
# STORE_FAST_LOAD_FAST (3.13+) stores its first name and reads its second.
_READS = frozenset(
    {
        "LOAD_FAST",
        "LOAD_FAST_CHECK",
        "LOAD_FAST_BORROW",
        "LOAD_FAST_LOAD_FAST",
        "LOAD_FAST_BORROW_LOAD_FAST_BORROW",
        "LOAD_NAME",
        "STORE_FAST_LOAD_FAST",
    }
)


def _handed_over(queryset: Any, frame: FrameType) -> bool:
    """Whether the counting function got the queryset from its caller, or may
    give it to another: it is an argument, a closure variable, or a local
    variable it reads more than once (``if not qs: raise ...; return qs``).

    A queryset that is no local variable (``if Book.objects.filter(...):``,
    ``len(self.books)``) is not handed over.
    """
    names = [name for name, value in frame.f_locals.items() if value is queryset]
    if not names:
        return False
    code = frame.f_code
    if code.co_name == "<module>":
        return True
    # several names for it (alias = qs = ...): their reads add up
    return any(_passed(code, name) for name in names) or (
        sum(_reads(code, name) for name in names) != 1
    )


def _passed(code: CodeType, name: str) -> bool:
    """Whether ``name`` is an argument or a closure variable of ``code``."""
    arguments = code.co_argcount + code.co_kwonlyargcount
    arguments += bool(code.co_flags & inspect.CO_VARARGS)
    arguments += bool(code.co_flags & inspect.CO_VARKEYWORDS)
    return (
        name in code.co_varnames[:arguments]
        or name in code.co_cellvars
        or name in code.co_freevars
    )


def _reads(code: CodeType, name: str) -> int:
    """How many instructions of ``code`` read the local ``name``."""
    key = (id(code), name)
    known = _read_counts.get(key)
    if known is not None:
        return known[1]
    count = 0
    for instruction in dis.get_instructions(code):
        if instruction.opname in _READS:
            value = instruction.argval
            if instruction.opname == "STORE_FAST_LOAD_FAST":
                names = value[1:] if isinstance(value, tuple) else ()
            else:
                names = value if isinstance(value, tuple) else (value,)
            count += names.count(name)
    if len(_read_counts) >= _MAX_READ_COUNTS:
        _read_counts.clear()
    _read_counts[key] = (code, count)
    return count


# --- findings -------------------------------------------------------------------


def unused(
    tracker: Tracker, sieve: blocks.Sieve | None = None
) -> list[list[Evaluation]]:
    """The evaluations whose rows nothing read, grouped by label and function.

    With a ``sieve``, a group whose evaluations all ran inside ``ignore()``
    blocks for the kind is left out.
    """
    groups: dict[tuple[str, str | None, str | None], list[Evaluation]] = {}
    for evaluation in tracker.evaluations.values():
        if evaluation.used or evaluation.escapes or evaluation.sql is None:
            continue
        if evaluation.queryset() is not None:
            continue  # still referenced: may be read after the test
        site = evaluation.callsite
        key = (
            evaluation.label,
            None if site is None else site.filename,
            None if site is None else site.function,
        )
        groups.setdefault(key, []).append(evaluation)
    return [
        group
        for group in groups.values()
        if sieve is None or not sieve.drops(group, KIND_QUERYSET_LEN)
    ]


def _summary(first: Evaluation, count: int) -> str:
    times = "" if count == 1 else f", {count} times"
    if first.how == HOW_LEN:
        rows = "" if first.rows is None else f" ({_rows(first.rows)})"
        return (
            f"  len() of a {first.model} queryset loaded all its rows{rows} only "
            f"to count them{times}; the rows were never used"
        )
    return (
        f"  bool() of a {first.model} queryset (if qs:, not qs) loaded all its rows "
        f"only to test whether there are any{times}; the rows were never used"
    )


def _rows(rows: int) -> str:
    return "1 row" if rows == 1 else f"{rows} rows"


def _fix(how: str) -> str:
    if how == HOW_LEN:
        return "  fix: qs.count(), which runs SELECT COUNT(*) and loads no rows"
    return "  fix: qs.exists(), which runs a SELECT with LIMIT 1 and loads no rows"


def findings(
    tracker: Tracker, *, cwd: Path | None = None, sieve: blocks.Sieve | None = None
) -> list[Finding]:
    """One finding per label and function whose rows nothing read; never raises."""
    if _broken:
        return []
    try:
        from .messages import (
            _display_path,
            _first_at,
            _stack,
            _truncate,
            docs_line,
            kind_line,
        )

        result: list[Finding] = []
        for group in unused(tracker, sieve):
            first = group[0]
            site = first.callsite
            stack = _stack(site, cwd)
            label = first.label
            message = "\n".join(
                [
                    _summary(first, len(group)),
                    *_first_at(site, stack, cwd),
                    kind_line(KIND_QUERYSET_LEN, label),
                    docs_line(KIND_QUERYSET_LEN),
                    f"  statement: {_truncate(first.sql or '')}",
                    _fix(first.how),
                    "  (experimental: if the rows are read later, or by code the plugin",
                    "  cannot see, leave it out with @pytest.mark.queryguard_ignore(",
                    f'      "{label}", kind="queryset-len")',
                    f"  or the queryguard_ignore pattern queryset-len {label})",
                ]
            )
            result.append(
                Finding(
                    kind=KIND_QUERYSET_LEN,
                    label=label,
                    file=None if site is None else _display_path(site.filename, cwd),
                    function=None if site is None else site.function,
                    message=message,
                    line=None if site is None else site.lineno,
                    count=len(group),
                    stack=stack,
                )
            )
        return result
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        _broke(exc, "the queryset-len check (analysis)")
        return []
