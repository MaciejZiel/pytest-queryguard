from __future__ import annotations


class QueryGuardWarning(UserWarning):
    """An N+1 query pattern found while running in ``warn`` mode."""


class QueryGuardInternalWarning(UserWarning):
    """A bug inside pytest-queryguard; the plugin stops measuring."""
