from __future__ import annotations


class QueryGuardWarning(UserWarning):
    """An N+1 query pattern or a missing index found in ``warn`` mode."""


class QueryGuardInternalWarning(UserWarning):
    """A bug inside pytest-queryguard; the plugin stops measuring.

    Also issued when a ``record_queries()`` block ends after such an error:
    its ``QueryLog`` may then be incomplete, and ``assert_no_nplusone()``
    raises ``RuntimeError`` instead of checking it.
    """


class QueryGuardDeprecationWarning(DeprecationWarning):
    """A pytest-queryguard option, name or behaviour that a later release removes.

    Not a ``QueryGuardWarning``: a ``filterwarnings`` entry that turns findings
    into errors (or ignores them) does not catch a deprecation, and the other
    way round.
    """
