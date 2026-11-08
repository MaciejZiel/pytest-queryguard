"""The plugin's own failure state and the scope numbers, shared by the ORM integrations.

Neither imports an ORM: the Django integration and the plugin use them in a
project that never imports SQLAlchemy, and SQLAlchemy is then never imported
(``backends``). ``listeners`` re-exports the functions under their old names.
"""

from __future__ import annotations

import itertools
import os
import traceback
from dataclasses import dataclass
from typing import Any

# The first exception a listener or step swallowed: measurement stops while
# it is set. Read inline (``errors._error is not None``) on the per-statement
# paths, so it stays a module attribute.
_error: BaseException | None = None
_failure: Failure | None = None

# Scope numbers for the repeat rules: one per Session, one per connection that
# no Session opened, one per Django request. Process-wide and never reused;
# the number is stored on the Session or Connection object, so it cannot be
# mixed up by a recycled id().
scopes = itertools.count(1)
_SCOPE_ATTR = "_queryguard_scope"


@dataclass(frozen=True, slots=True)
class Failure:
    """What went wrong inside the plugin, for the warning and the summary."""

    source: str  # the listener or step that raised
    error: BaseException
    raised_at: str | None  # "file.py:12 in function", innermost frame

    @property
    def summary(self) -> str:
        return f"{type(self.error).__name__}: {self.error}"


def internal_error() -> BaseException | None:
    """The first exception swallowed by a listener, if any."""
    return _error


def internal_failure() -> Failure | None:
    """Which listener or step failed first, and why; None without an error.

    The error itself is the one ``internal_error()`` returns. When it was set
    without a source (``swap_error()``), the source is "unknown".
    """
    if _error is None:
        return None
    if _failure is not None and _failure.error is _error:
        return _failure
    return Failure("unknown", _error, _innermost(_error))


def _innermost(exc: BaseException) -> str | None:
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return None
    last = frames[-1]
    return f"{os.path.basename(last.filename)}:{last.lineno} in {last.name}"


def fail(exc: Exception, source: str) -> None:
    """Record ``exc`` as the plugin's failure; the first one wins."""
    global _error, _failure
    if _error is None:
        _error = exc
        _failure = Failure(source, exc, _innermost(exc))


def record_error(exc: Exception, source: str = "plugin") -> None:
    """Disable measurement after a bug outside the listeners (first error wins)."""
    fail(exc, source)


def swap_error(new: BaseException | None) -> BaseException | None:
    """Replace the recorded error and return the previous one.

    The plugin clears it for each session and restores the outer session's
    error afterwards, so in-process nested sessions stay independent.
    """
    global _error
    old, _error = _error, new
    return old


def reset() -> None:
    global _error, _failure
    _error = None
    _failure = None


def scope_of(owner: Any) -> int | None:
    """The scope number of a Session or Connection, assigned on first use."""
    try:
        scope = owner.__dict__.get(_SCOPE_ATTR)
        if scope is None:
            scope = next(scopes)
            setattr(owner, _SCOPE_ATTR, scope)
        return scope  # type: ignore[no-any-return]
    except (AttributeError, TypeError):
        return None
