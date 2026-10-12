from __future__ import annotations

import warnings


class QueryGuardWarning(UserWarning):
    """An N+1 query pattern or a missing index found in ``warn`` mode."""


class QueryGuardInternalWarning(UserWarning):
    """A bug inside pytest-queryguard; the plugin stops measuring.

    Also issued when a ``record_queries()`` block ends after such an error:
    its ``QueryLog`` may then be incomplete, and ``assert_no_nplusone()``
    raises ``RuntimeError`` instead of checking it.
    """


def warn_internal(warning: QueryGuardInternalWarning, stacklevel: int = 1) -> None:
    """Issue ``warning``; a warnings "error" filter never raises it.

    A bug in the plugin must not fail the user's test. With ``-W error`` or
    ``filterwarnings = error`` the warning would be raised as an exception, so
    it is issued once more with an "always" filter for its class: it is still
    shown and recorded, never raised. An "ignore" filter is respected.
    """
    try:
        warnings.warn(warning, stacklevel=stacklevel + 1)
    except QueryGuardInternalWarning:
        with warnings.catch_warnings():
            warnings.simplefilter("always", QueryGuardInternalWarning)
            warnings.warn(warning, stacklevel=stacklevel + 1)
