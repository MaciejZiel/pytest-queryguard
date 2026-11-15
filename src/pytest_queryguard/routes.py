"""Route budgets in pytest: the most statements one request of a route may run.

``queryguard_route_budgets`` (and the ``queryguard_route_budgets`` marker)
gives route patterns a statement budget, as the in-app mode's ``budgets``
does; ``queryguard_route_default_budget`` is the budget of every other
request. Each request a test sends through a test client (or any other
caller) of a Starlette, FastAPI, Litestar, Flask or Django application is a
unit of its own, and the test fails when one goes over its budget.

How a request is counted:

* Starlette, FastAPI and Litestar: from the call of the application to its
  return, in the request's context, so a streamed body and background tasks
  count too. The route is the template Starlette or FastAPI put in the scope
  (with the prefix of a mounted sub-application) or Litestar's
  ``path_template``.
* Flask: from the call of the application until the server (the test client)
  has read or closed the body. The route is the Flask rule.
* Django: the statements Django's request scope (``django_backend``) gives to
  the request, which already follows a request from ``request_started`` to
  ``request_finished``. The route is ``resolver_match.route``.

Nothing is patched until a test has route budgets; a request outside a test
call, or of a test without route budgets, is not measured.
"""

from __future__ import annotations

import contextvars
import functools
import re
import sys
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from . import collector as collectors
from .collector import QueryCollector, RequestScope
from .errors import record_error
from .events import StatementEvent
from .messages import format_budget

INI_BUDGETS = "queryguard_route_budgets"
INI_DEFAULT = "queryguard_route_default_budget"
MARKER = "queryguard_route_budgets"

_ORIGINAL = "_queryguard_route_original"
_METHOD = re.compile(r"[A-Za-z][A-Za-z-]*")  # GET, M-SEARCH, VERSION-CONTROL


# --- matching, shared with the in-app mode ---------------------------------


def split_key(key: str) -> tuple[str, str]:
    """``"GET /a b"`` -> ``("GET", "/a b")``; ``"/a b"`` -> ``("", "/a b")``.

    The first word is a method only when it looks like one (letters and
    hyphens), so a path with spaces keeps them; any
    whitespace separates the method.
    """
    head, *rest = key.strip().split(None, 1)
    if rest and _METHOD.fullmatch(head):
        return head.upper(), rest[0].strip()
    return "", key.strip()


def matching_key(
    budgets: Mapping[str, int],
    method: str | None,
    route: str | None,
    path: str | None,
) -> str | None:
    """The first key of ``budgets`` that matches the request, or None.

    The path of a key is an ``fnmatch`` pattern matched against the route
    template and the request path; a key with a method matches that method
    only.
    """
    names = [name for name in (route, path) if name]
    for key in budgets:
        want_method, pattern = split_key(key)
        if want_method and (method or "").upper() != want_method:
            continue
        if any(fnmatchcase(name, pattern) for name in names):
            return key
    return None


# --- configuration ----------------------------------------------------------


@dataclass(frozen=True)
class RouteBudgets:
    """The route budgets of one test: patterns first, then the default."""

    budgets: dict[str, int]
    default: int | None = None
    # key -> where it comes from, for the failure text
    sources: dict[str, str] = field(default_factory=dict)
    default_source: str = INI_DEFAULT

    def limit(
        self, method: str | None, route: str | None, path: str | None
    ) -> tuple[int, str] | None:
        """(budget, where it comes from) of a request; None when it has none."""
        key = matching_key(self.budgets, method, route, path)
        if key is not None:
            return self.budgets[key], f'"{key}" in {self.sources.get(key, INI_BUDGETS)}'
        if self.default is not None:
            return self.default, self.default_source
        return None


def _check_limit(limit: object, what: str) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError(f"{what} must be an integer >= 0, got {limit!r}")
    return limit


def parse_lines(lines: Iterable[str]) -> dict[str, int]:
    """``"GET /books/{id}=5"`` lines as a dict; raises ValueError on a bad line.

    The limit follows the last ``=``; blank lines and ``#`` comments are
    skipped.
    """
    budgets: dict[str, int] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.rpartition("=")
        key = key.strip()
        if not sep or not key:
            raise ValueError(
                f"{line!r} is not a route budget: write it as PATTERN=LIMIT, "
                'for example "GET /books/{id}=5"'
            )
        try:
            limit = int(value.strip())
        except ValueError:
            limit = -1
        if limit < 0:
            raise ValueError(
                f"the limit of {key!r} must be an integer >= 0, got {value.strip()!r}"
            )
        budgets.setdefault(key, limit)  # the first line of a key wins
    return budgets


def parse_default(raw: object) -> int | None:
    """The ``queryguard_route_default_budget`` value; raises ValueError."""
    if isinstance(raw, str):
        if not raw.strip():
            return None
        try:
            return _check_limit(int(raw.strip()), INI_DEFAULT)
        except ValueError:
            pass
    elif isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
        return raw
    raise ValueError(f"{INI_DEFAULT} must be an integer >= 0, got {raw!r}")


def from_marker(
    marker: Any, nodeid: str, budgets: Mapping[str, int], default: int | None
) -> RouteBudgets:
    """The budgets of a test with the marker, over those of the ini file.

    ``@pytest.mark.queryguard_route_budgets({"GET /books/{id}": 5}, default=10)``:
    its patterns are matched before those of the ini file, and ``default=``
    (None for no default) replaces the ini file's default.
    """
    name = f"{MARKER} on {nodeid}"
    if len(marker.args) > 1 or set(marker.kwargs) - {"default"}:
        raise ValueError(
            f"{name} takes a mapping of route patterns to limits and default="
        )
    own: dict[str, int] = {}
    if marker.args:
        mapping = marker.args[0]
        if not isinstance(mapping, Mapping):
            raise ValueError(
                f"{name} needs a mapping of route patterns to limits, got {mapping!r}"
            )
        for key, limit in mapping.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f"{name}: a pattern must be a non-empty string")
            own[key.strip()] = _check_limit(limit, f"{name}: the limit of {key!r}")
    sources = dict.fromkeys(own, f"the {MARKER} marker")
    merged = dict(own)
    for key, limit in budgets.items():
        if key not in merged:
            merged[key] = limit
            sources[key] = INI_BUDGETS
    default_source = INI_DEFAULT
    if "default" in marker.kwargs:
        default = marker.kwargs["default"]
        if default is not None:
            _check_limit(default, f"{name}: default")
        default_source = f"the default of the {MARKER} marker"
    return RouteBudgets(merged, default, sources, default_source)


# --- the requests of the running test ----------------------------------------


class _Unit:
    """One request: its statements, counted in the request's context."""

    __slots__ = ("collector", "finished", "method", "opened", "path", "route", "state")

    def __init__(self, state: _State, method: str | None, path: str | None) -> None:
        self.state = state
        self.method = method
        self.path = path
        self.route: str | None = None
        self.collector = QueryCollector(statements_only=True)
        self.collector.max_events = state.max_events
        self.opened = False
        self.finished = False

    def enter(self) -> tuple[Any, Any]:
        """Count this context's statements for the request until ``leave``."""
        if not self.opened:
            collectors.open_local(self.collector)
            self.opened = True
            with self.state.lock:
                self.state.units.append(self)
        return collectors.enter_local(self.collector), _current.set(self)

    def leave(self, tokens: tuple[Any, Any]) -> None:
        local, unit = tokens
        collectors.exit_local(self.collector, local)
        try:
            _current.reset(unit)
        except ValueError:
            if _current.get() is self:
                _current.set(None)

    def finish(self) -> None:
        """Stop counting; statements of a task it left running are dropped."""
        if self.finished:
            return
        self.finished = True
        if self.opened:
            collectors.pop_local(self.collector)


@dataclass(eq=False)
class _State:
    budgets: RouteBudgets
    max_events: int | None
    units: list[_Unit] = field(default_factory=list)
    # Django: request scope -> (method, path, route)
    django: dict[int, tuple[str | None, str | None, str | None]] = field(
        default_factory=dict
    )
    lock: threading.Lock = field(default_factory=threading.Lock)


# Process-wide, as the collector stack: the application may run in another
# thread than the test (the portal thread of Starlette's TestClient).
_state: _State | None = None
# The request being handled in this context, so a sub-application (a mounted
# Starlette app, an app inside an app) is not counted twice.
_current: contextvars.ContextVar[_Unit | None] = contextvars.ContextVar(
    "queryguard_route_unit", default=None
)


def begin(budgets: RouteBudgets, max_events: int | None) -> None:
    """Measure the requests of the test that starts now."""
    global _state
    install()
    _state = _State(budgets, max_events)


def end(test: QueryCollector | None, root: Path | None) -> list[str]:
    """Stop measuring; the failure text of each route over its budget.

    ``test`` is the test's own collector: Django's requests are counted from
    the request scopes of its statements.
    """
    global _state
    state, _state = _state, None
    if state is None:
        return []
    with state.lock:
        units = list(state.units)
    for unit in units:
        unit.finish()  # a body never read to its end, a request still running
    requests = [
        _Request(u.method, u.path, u.route, list(u.collector.statements), u.collector)
        for u in units
    ]
    if test is not None:
        requests.extend(_django_requests(state, test))
    return _report(state.budgets, requests, root)


@dataclass
class _Request:
    method: str | None
    path: str | None
    route: str | None
    statements: list[StatementEvent]
    collector: QueryCollector | None = None

    @property
    def count(self) -> int:
        if self.collector is not None:
            return self.collector.statement_count
        return len(self.statements)

    @property
    def db_time(self) -> float:
        if self.collector is not None:
            return self.collector.db_time
        return sum(s.duration or 0.0 for s in self.statements)


def _django_requests(state: _State, test: QueryCollector) -> list[_Request]:
    by_scope: dict[int, list[StatementEvent]] = {}
    for stmt in list(test.statements):
        scope = stmt.scope
        if isinstance(scope, RequestScope):
            by_scope.setdefault(scope, []).append(stmt)
    with state.lock:
        named = dict(state.django)
    requests = []
    for scope in sorted(by_scope.keys() | named.keys()):
        # a request answered before URL resolution (by a middleware) has no
        # name: only the default budget applies to it
        method, path, route = named.get(scope, (None, None, None))
        requests.append(_Request(method, path, route, by_scope.get(scope, [])))
    return requests


def _report(
    budgets: RouteBudgets, requests: list[_Request], root: Path | None
) -> list[str]:
    """One failure text per route (or path) with requests over its budget."""
    over: dict[tuple[str, str, int, str], list[_Request]] = {}
    for request in requests:
        found = budgets.limit(request.method, request.route, request.path)
        if found is None:
            continue
        limit, source = found
        if request.count <= limit:
            continue
        name = request.route or request.path or "a request without a route"
        key = (request.method or "", name, limit, source)
        over.setdefault(key, []).append(request)
    texts = []
    for (method, name, limit, source), group in over.items():
        worst = max(group, key=lambda r: r.count)
        texts.append(_text(method, name, limit, source, worst, len(group), root))
    return texts


def _text(
    method: str,
    name: str,
    limit: int,
    source: str,
    worst: _Request,
    over: int,
    root: Path | None,
) -> str:
    route = f"{method} {name}".strip()
    request = f"{worst.method or ''} {worst.path or ''}".strip() or "it"
    noun = "statement" if limit == 1 else "statements"
    lines = [
        (
            f"queryguard: route budget: {route} may run at most {limit} {noun} "
            f"per request, {request} ran {worst.count}"
        ),
        f"  (the limit is {source})",
    ]
    if over > 1:
        lines.append(
            f"  {over} requests of this route went over; the one with the most "
            "statements:"
        )
    body = format_budget(
        limit,
        worst.statements,
        cwd=root,
        total=worst.count,
        db_time=worst.db_time,
    ).split("\n")
    lines.extend(body[1:])  # the statements and timing, under our own heading
    return "\n".join(lines)


# --- the frameworks ---------------------------------------------------------


def _start(method: str | None, path: str | None) -> _Unit | None:
    """A unit for a request starting in this context; None when not measured."""
    state = _state
    if state is None or _current.get() is not None:
        return None
    return _Unit(state, method, path)


def _asgi_route(scope: Any, root_path: str) -> str | None:
    """The route template of an ASGI request: Litestar's, Starlette's or FastAPI's."""
    template = scope.get("path_template")  # Litestar
    if isinstance(template, str) and template:
        return template
    matched = scope.get("route")
    route = getattr(matched, "path", None)  # Starlette, FastAPI
    # A mounted sub-application's route is relative to its mount; the root
    # path below the application's own is the mount's.
    prefix = scope.get("root_path") or ""
    mount = (
        prefix[len(root_path) :]
        if isinstance(prefix, str) and prefix.startswith(root_path)
        else ""
    )
    if not isinstance(route, str):
        # FastAPI puts back the route it found before a Mount: name the
        # request by its mount (a plain ASGI application mounted)
        return mount or None
    if hasattr(matched, "routes"):
        # The Mount itself: no route inside it matched (a plain ASGI app, a
        # 404 of a sub-application). Its own path is already in the prefix.
        return mount or route
    return mount + route


def _asgi(original: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(original)
    async def __call__(self: Any, scope: Any, receive: Any, send: Any) -> None:
        unit = None
        try:
            if scope.get("type") == "http":
                unit = _start(scope.get("method"), scope.get("path"))
                root_path = scope.get("root_path") or ""
                if unit is not None:
                    tokens = unit.enter()
        except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
            record_error(exc, "route budgets (start of a request)")
            unit = None
        if unit is None:
            await original(self, scope, receive, send)
            return
        try:
            await original(self, scope, receive, send)
        finally:
            try:
                unit.leave(tokens)
                # a WSGI application mounted inside (Flask) may have named it
                if unit.route is None:
                    unit.route = _asgi_route(scope, root_path)
                unit.finish()
            except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
                record_error(exc, "route budgets (end of a request)")

    return __call__


def _wsgi(original: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(original)
    def __call__(self: Any, environ: Any, start_response: Any) -> Any:
        try:
            unit = _start(
                environ.get("REQUEST_METHOD"), environ.get("PATH_INFO") or "/"
            )
            tokens = unit.enter() if unit is not None else None
        except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
            record_error(exc, "route budgets (start of a request)")
            unit = tokens = None
        if unit is None or tokens is None:
            return original(self, environ, start_response)
        try:
            result = original(self, environ, start_response)
        except BaseException:
            unit.leave(tokens)
            unit.finish()
            raise
        unit.leave(tokens)
        return _Body(result, unit)

    return __call__


class _Body:
    """A WSGI response body, made in the request's context; the request ends
    when it has been read to its end or closed."""

    def __init__(self, result: Iterable[bytes], unit: _Unit) -> None:
        self._result = result
        self._iter: Iterator[bytes] | None = None
        self._unit = unit
        self._closed = False

    def __iter__(self) -> Iterator[bytes]:
        return self

    def __next__(self) -> bytes:
        if self._closed:
            raise StopIteration
        tokens = self._unit.enter()
        try:
            if self._iter is None:
                self._iter = iter(self._result)
            chunk = next(self._iter, None)
        finally:
            self._unit.leave(tokens)
        if chunk is None:
            # cleanup callbacks (Flask's call_on_close) count too
            self.close()
            raise StopIteration
        return chunk

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            close = getattr(self._result, "close", None)
            if close is not None:
                tokens = self._unit.enter()
                try:
                    close()
                finally:
                    self._unit.leave(tokens)
        finally:
            self._unit.finish()


def _on_flask_request(sender: Any, **extra: Any) -> None:
    unit = _current.get()
    if unit is None:
        return
    try:
        from flask import request

        rule = request.url_rule
        if rule is not None:
            # with the prefix it is mounted at, if any
            unit.route = request.script_root + rule.rule
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        record_error(exc, "route budgets (Flask route)")


def _resolve_request(original: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(original)
    def resolve_request(self: Any, request: Any) -> Any:
        try:
            return original(self, request)
        finally:
            _note_django(request)

    return resolve_request


def _note_django(request: Any) -> None:
    state = _state
    if state is None:
        return
    try:
        from . import django_backend

        scope = django_backend._request.get()
        if not isinstance(scope, RequestScope):
            return
        match = getattr(request, "resolver_match", None)
        route = getattr(match, "route", None)
        if isinstance(route, str) and route:
            route = route if route.startswith("^") else "/" + route
        else:
            route = None
        with state.lock:
            state.django[scope] = (request.method, request.path, route)
    except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
        record_error(exc, "route budgets (Django route)")


def _patch(owner: Any, name: str, make: Callable[[Any], Any]) -> None:
    """Replace ``owner.name`` with ``make(original)``, once."""
    original = owner.__dict__.get(name)
    if original is None or getattr(original, _ORIGINAL, None) is not None:
        return
    wrapper = make(original)
    setattr(wrapper, _ORIGINAL, original)
    setattr(owner, name, wrapper)


# The modules patched already, by identity: a new import of a framework
# (pytester sessions purge sys.modules) is patched again.
_patched: dict[str, object] = {}


def install() -> None:
    """Patch the frameworks imported so far; cheap after the first time."""
    for name, patch in _FRAMEWORKS:
        module = sys.modules.get(name)
        if module is None or _patched.get(name) is module:
            continue
        try:
            patch(module)
        except Exception as exc:  # noqa: BLE001  # a plugin bug must never break the user suite
            record_error(exc, f"route budgets ({name})")
        _patched[name] = module


def _patch_starlette(module: Any) -> None:
    _patch(module.Starlette, "__call__", _asgi)


def _patch_litestar(module: Any) -> None:
    _patch(module.Litestar, "__call__", _asgi)


def _patch_flask(module: Any) -> None:
    _patch(module.Flask, "__call__", _wsgi)
    signals = sys.modules.get("flask.signals")
    if signals is not None:
        signals.request_started.connect(_on_flask_request, weak=False)


def _patch_django(module: Any) -> None:
    _patch(module.BaseHandler, "resolve_request", _resolve_request)


_FRAMEWORKS: tuple[tuple[str, Callable[[Any], None]], ...] = (
    ("starlette.applications", _patch_starlette),
    ("litestar.app", _patch_litestar),
    ("flask.app", _patch_flask),
    ("django.core.handlers.base", _patch_django),
)
