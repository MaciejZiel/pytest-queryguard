"""In-application mode: the N+1 rules and query budgets per HTTP request.

For development servers and staging, not for tests (use the pytest plugin)
and not for production. Importing this module registers nothing and does not
need pytest to be running::

    from pytest_queryguard import app as queryguard

    queryguard.watch(engine)
    asgi_app = queryguard.QueryGuardASGI(asgi_app, budgets={"GET /orders": 3})

See docs/in-app.md.
"""

from __future__ import annotations

from ..api import Query
from ..events import CallSite
from ..findings import Finding
from ._core import RequestReport, Settings, internal_error, set_route, track
from ._listeners import is_watched, unwatch, watch

__all__ = [
    "CallSite",
    "Finding",
    "Query",
    "RequestReport",
    "Settings",
    "internal_error",
    "is_watched",
    "set_route",
    "track",
    "unwatch",
    "watch",
]
