from __future__ import annotations


class QueryGuardWarning(UserWarning):
    """An N+1 query pattern or a missing index found in ``warn`` mode."""


class QueryGuardInternalWarning(UserWarning):
    """A bug inside pytest-queryguard; the plugin stops measuring.

    Also issued when a ``record_queries()`` block ends after such an error:
    its ``QueryLog`` may then be incomplete, and ``assert_no_nplusone()``
    raises ``RuntimeError`` instead of checking it.
    """
