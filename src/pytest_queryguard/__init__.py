from __future__ import annotations

from .api import QueryBudgetExceeded, max_queries
from .warnings import QueryGuardInternalWarning, QueryGuardWarning

__version__ = "0.1.0.dev0"

__all__ = [
    "QueryBudgetExceeded",
    "QueryGuardInternalWarning",
    "QueryGuardWarning",
    "__version__",
    "max_queries",
]
