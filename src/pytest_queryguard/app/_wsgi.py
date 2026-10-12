"""WSGI middleware: one measured unit per request (Flask, Django, any WSGI app).

The request ends when the server closes the response iterable, so statements
run while a streamed body is produced count too. WSGI has no route
templates; budgets match the path unless the application calls
``set_route()``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from types import TracebackType
from typing import Any

from . import _core, _panel
from ._core import RequestReport, Settings, Tracker

Environ = dict[str, Any]
ExcInfo = tuple[type[BaseException], BaseException, TracebackType]
StartResponse = Callable[..., Any]
WSGIApp = Callable[[Environ, StartResponse], Iterable[bytes]]


class QueryGuardWSGI:
    """Run the N+1 rules and budgets on every request of a WSGI app.

    Wrap the application callable, for Flask ``app.wsgi_app``. Takes a
    ``Settings`` or its fields as keyword arguments. With ``panel=True`` it
    also serves the development panel at ``panel_path``; by default only to
    loopback clients. Never enable the panel in production.
    """

    def __init__(
        self,
        app: WSGIApp,
        settings: Settings | None = None,
        *,
        panel: bool = False,
        panel_path: str = "/_queryguard",
        panel_history: int = 50,
        panel_loopback_only: bool = True,
        **options: Any,
    ) -> None:
        self.app = app
        self.settings = _core._settings(settings, options)
        self.panel = bool(panel)
        self.panel_path = _panel.normalize_path(panel_path)
        self.panel_loopback_only = panel_loopback_only
        self.history = _panel.History(panel_history) if self.panel else None

    def __call__(
        self, environ: Environ, start_response: StartResponse
    ) -> Iterable[bytes]:
        if self.history is not None:
            target = _panel.route(self.panel_path, environ.get("PATH_INFO", ""))
            if target is not None:
                return self._serve_panel(environ, start_response, target)
        tracker = _start(self.settings, environ)
        if tracker is None:
            return self.app(environ, start_response)
        report = tracker.report

        def start(status: str, headers: Any, exc_info: ExcInfo | None = None) -> Any:
            try:
                report.status = int(status.split(None, 1)[0])
            except (ValueError, IndexError, AttributeError):
                pass
            if exc_info is None:
                return start_response(status, headers)
            return start_response(status, headers, exc_info)

        try:
            result = self.app(environ, start)
        except BaseException:
            if report.status is None:
                report.status = 500
            tracker.detach()
            self._finish(tracker)
            raise
        tracker.detach()
        return _Body(result, tracker, self)

    def _finish(self, tracker: Tracker) -> None:
        if tracker.finished:
            return
        report = tracker.finish()
        if self.history is not None:
            self.history.add(report)

    def _serve_panel(
        self, environ: Environ, start_response: StartResponse, target: str
    ) -> Iterable[bytes]:
        method = environ.get("REQUEST_METHOD")
        if method not in ("GET", "HEAD"):
            status, body, kind = (
                "405 Method Not Allowed",
                b"method not allowed",
                "text/plain",
            )
        elif not _panel.allowed(environ.get("REMOTE_ADDR"), self.panel_loopback_only):
            status, body, kind = (
                "403 Forbidden",
                b"the panel answers loopback clients only",
                "text/plain",
            )
        elif target == "html":
            root = environ.get("SCRIPT_NAME", "") or ""
            json_url = f"{root}{self.panel_path}/requests.json"
            status, body, kind = "200 OK", _panel.html_body(json_url), "text/html"
        elif target == "json":
            assert self.history is not None
            status, body, kind = (
                "200 OK",
                _panel.json_body(self.history),
                "application/json",
            )
        else:
            status, body, kind = "404 Not Found", b"not found", "text/plain"
        headers = [
            ("Content-Type", f"{kind}; charset=utf-8"),
            ("Content-Length", str(len(body))),
            *_panel.HEADERS,
        ]
        start_response(status, headers)
        return [b"" if method == "HEAD" else body]

    @property
    def reports(self) -> tuple[RequestReport, ...]:
        """The reports the panel holds, oldest first; empty without the panel."""
        return self.history.snapshot() if self.history is not None else ()


class _Body:
    """The response iterable: re-enters the request's context for each chunk."""

    def __init__(
        self, result: Iterable[bytes], tracker: Tracker, owner: QueryGuardWSGI
    ) -> None:
        self._result = result
        self._iter: Iterator[bytes] | None = None
        self._tracker = tracker
        self._owner = owner

    def __iter__(self) -> Iterator[bytes]:
        return self

    def __next__(self) -> bytes:
        if self._iter is None:
            self._iter = iter(self._result)
        token = _core.current.set(self._tracker.active)
        try:
            chunk = next(self._iter)
        except StopIteration:
            # The body is complete. Servers close it next; a test client may
            # not, so the request ends here already.
            _core.current.reset(token)
            self._owner._finish(self._tracker)
            raise
        except BaseException:
            _core.current.reset(token)
            raise
        _core.current.reset(token)
        return chunk

    def close(self) -> None:
        try:
            close = getattr(self._result, "close", None)
            if close is not None:
                token = _core.current.set(self._tracker.active)
                try:
                    close()
                finally:
                    _core.current.reset(token)
        finally:
            self._owner._finish(self._tracker)


def _start(settings: Settings, environ: Environ) -> Tracker | None:
    if _core._error is not None:
        return None
    try:
        method = environ.get("REQUEST_METHOD")
        path = environ.get("PATH_INFO") or "/"
        tracker = Tracker(settings, f"{method} {path}", method=method, path=path)
        tracker.start()
        return tracker
    except Exception as exc:  # noqa: BLE001  # never break the application
        _core.fail(exc, "the start of a request")
        return None
