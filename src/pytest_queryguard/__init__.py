from __future__ import annotations

from .api import (
    NPlusOneDetected,
    Query,
    QueryBudgetExceeded,
    QueryLog,
    max_db_time,
    max_queries,
    record_queries,
)
from .events import CallSite
from .warnings import (
    QueryGuardDeprecationWarning,
    QueryGuardInternalWarning,
    QueryGuardWarning,
)

__version__ = "0.4.0rc2"

__all__ = [
    "CallSite",
    "NPlusOneDetected",
    "Query",
    "QueryBudgetExceeded",
    "QueryGuardDeprecationWarning",
    "QueryGuardInternalWarning",
    "QueryGuardWarning",
    "QueryLog",
    "__version__",
    "max_db_time",
    "max_queries",
    "record_queries",
]
