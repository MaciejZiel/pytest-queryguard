"""Each request keeps its own statements across every phase of its lifecycle.

One matrix for the bugs that came from a request's scope ending before its
work did (#203, #211, #386): a body produced after the handler returned, a
close() callback, a file the server sends, a background task, an error half
way through a body, a HEAD request, a mounted application, a middleware
around ours, the threads of a test client, a unit that spans a generator.

Each row sends three requests whose phase runs ``SELECT <marker>``, then
checks that every request reported exactly its own markers, and that nothing
leaked into an enclosing ``track()``. The asynchronous rows also run their
requests concurrently, so a scope two requests shared would show.
See "When code scopes something to a request" in CONTRIBUTING.md.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from flask import Flask, Response, stream_with_context
from sqlalchemy import text
from sqlalchemy.engine import Engine
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    PlainTextResponse,
    StreamingResponse,
)
from starlette.responses import (
    Response as StarletteResponse,
)
from starlette.routing import Mount, Route
from starlette.testclient import TestClient
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.test import Client as WerkzeugClient

from pytest_queryguard import app as queryguard
from pytest_queryguard.app import QueryGuardASGI, QueryGuardWSGI, RequestReport

REQUESTS = 3


def _execute(engine: Engine, marker: int) -> None:
    with engine.connect() as connection:
        connection.execute(text(f"SELECT {marker}")).scalar_one()


def _markers(report: RequestReport) -> list[str]:
    return [q.sql for q in report.statements]


def _expect(reports: tuple[RequestReport, ...], per_request: int) -> None:
    """Request ``i`` ran ``SELECT 100*i + k`` for k in 1..per_request, only those."""
    seen = sorted(_markers(r) for r in reports)
    expected = sorted(
        [f"SELECT {100 * i + k}" for k in range(1, per_request + 1)]
        for i in range(1, REQUESTS + 1)
    )
    assert seen == expected


# --- ASGI (Starlette; FastAPI is Starlette underneath) -----------------------


def _asgi_routes(engine: Engine, tmp_path: Path) -> list[Route]:
    """Each endpoint takes the request number ``i`` and runs markers 100*i+k."""
    download = tmp_path / "download.txt"
    download.write_bytes(b"file body")

    def run(i: int, k: int) -> None:
        _execute(engine, 100 * i + k)

    async def endpoint_async(request: Request) -> StarletteResponse:
        run(int(request.path_params["i"]), 1)
        return PlainTextResponse("ok")

    def endpoint_sync(request: Request) -> StarletteResponse:
        run(int(request.path_params["i"]), 1)
        return PlainTextResponse("ok")

    async def stream_async(request: Request) -> StarletteResponse:
        i = int(request.path_params["i"])

        async def body() -> AsyncIterator[bytes]:
            for k in (1, 2):
                run(i, k)
                await asyncio.sleep(0)
                yield b"part "

        return StreamingResponse(body())

    async def stream_sync(request: Request) -> StarletteResponse:
        i = int(request.path_params["i"])

        def body() -> Iterator[bytes]:  # Starlette runs it in its thread pool
            for k in (1, 2):
                run(i, k)
                yield b"part "

        return StreamingResponse(body())

    async def background_async(request: Request) -> StarletteResponse:
        i = int(request.path_params["i"])

        async def later() -> None:
            await asyncio.sleep(0)
            run(i, 2)

        run(i, 1)
        return PlainTextResponse("ok", background=BackgroundTask(later))

    async def background_sync(request: Request) -> StarletteResponse:
        i = int(request.path_params["i"])
        run(i, 1)
        return PlainTextResponse("ok", background=BackgroundTask(run, i, 2))

    async def file(request: Request) -> StarletteResponse:
        run(int(request.path_params["i"]), 1)
        return FileResponse(download)

    return [
        Route("/endpoint-async/{i}", endpoint_async),
        Route("/endpoint-sync/{i}", endpoint_sync),
        Route("/stream-async/{i}", stream_async),
        Route("/stream-sync/{i}", stream_sync),
        Route("/background-async/{i}", background_async),
        Route("/background-sync/{i}", background_sync),
        Route("/file/{i}", file),
    ]


# (path, statements per request, expected body)
ASGI_PHASES = {
    "endpoint-async": (1, b"ok"),
    "endpoint-sync": (1, b"ok"),
    "stream-async": (2, b"part part "),
    "stream-sync": (2, b"part part "),
    "background-async": (2, b"ok"),
    "background-sync": (2, b"ok"),
    "file": (1, b"file body"),
}


class _PassThrough(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Any) -> StarletteResponse:
        response: StarletteResponse = await call_next(request)
        return response


def _asgi_stack(
    engine: Engine, tmp_path: Path, layout: str
) -> tuple[Any, QueryGuardASGI, str]:
    """The application to call, our middleware in it, and the URL prefix."""
    routes = _asgi_routes(engine, tmp_path)
    if layout == "plain":
        guard = QueryGuardASGI(Starlette(routes=routes), panel=True, report=[])
        return guard, guard, ""
    if layout == "mounted":
        # Our middleware around a sub-application mounted in another.
        guard = QueryGuardASGI(Starlette(routes=routes), panel=True, report=[])
        return Starlette(routes=[Mount("/sub", app=guard)]), guard, "/sub"
    if layout == "base-http-middleware-outside":
        guard = QueryGuardASGI(Starlette(routes=routes), panel=True, report=[])
        outer = Starlette(
            routes=[Mount("/", app=guard)], middleware=[Middleware(_PassThrough)]
        )
        return outer, guard, ""
    assert layout == "base-http-middleware-inside"
    inner = Starlette(routes=routes, middleware=[Middleware(_PassThrough)])
    guard = QueryGuardASGI(inner, panel=True, report=[])
    return guard, guard, ""


ASGI_LAYOUTS = [
    "plain",
    "mounted",
    "base-http-middleware-outside",
    "base-http-middleware-inside",
]


@pytest.mark.parametrize("layout", ASGI_LAYOUTS)
@pytest.mark.parametrize("phase", list(ASGI_PHASES))
def test_asgi_test_client_requests_keep_their_statements(
    watched: Engine, tmp_path: Path, phase: str, layout: str
) -> None:
    per_request, body = ASGI_PHASES[phase]
    app, guard, prefix = _asgi_stack(watched, tmp_path, layout)
    with TestClient(app) as client:  # the app runs in the client's thread
        for i in range(1, REQUESTS + 1):
            response = client.get(f"{prefix}/{phase}/{i}")
            assert (response.status_code, response.content) == (200, body)
    _expect(guard.reports, per_request)
    assert all(r.status == 200 for r in guard.reports)


@pytest.mark.parametrize("layout", ASGI_LAYOUTS)
@pytest.mark.parametrize("phase", list(ASGI_PHASES))
def test_asgi_concurrent_requests_keep_their_statements(
    watched: Engine, tmp_path: Path, phase: str, layout: str
) -> None:
    per_request, body = ASGI_PHASES[phase]
    app, guard, prefix = _asgi_stack(watched, tmp_path, layout)

    async def main() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await asyncio.gather(
                *(client.get(f"{prefix}/{phase}/{i}") for i in range(1, REQUESTS + 1))
            )

    with queryguard.track("outer", report=[]) as outer:
        responses = asyncio.run(main())
    assert [(r.status_code, r.content) for r in responses] == [(200, body)] * REQUESTS
    _expect(guard.reports, per_request)
    assert _markers(outer) == []


def test_asgi_head_request_is_measured_without_a_body(
    watched: Engine, tmp_path: Path
) -> None:
    guard = QueryGuardASGI(
        Starlette(routes=_asgi_routes(watched, tmp_path)), panel=True, report=[]
    )
    with TestClient(guard) as client:
        for i in range(1, REQUESTS + 1):
            response = client.head(f"/endpoint-async/{i}")
            assert (response.status_code, response.content) == (200, b"")
    _expect(guard.reports, 1)
    assert {r.method for r in guard.reports} == {"HEAD"}


def test_asgi_error_half_way_through_a_body_ends_the_request(
    watched: Engine,
) -> None:
    async def broken(request: Request) -> StarletteResponse:
        i = int(request.path_params["i"])

        async def body() -> AsyncIterator[bytes]:
            _execute(watched, 100 * i + 1)
            yield b"part "
            raise RuntimeError("body bug")

        return StreamingResponse(body())

    guard = QueryGuardASGI(
        Starlette(routes=[Route("/broken/{i}", broken)]), panel=True, report=[]
    )
    with TestClient(guard) as client:
        for i in range(1, REQUESTS + 1):
            with pytest.raises(RuntimeError, match="body bug"):
                client.get(f"/broken/{i}")
    _expect(guard.reports, 1)
    # The status line was sent before the error: the client saw a 200.
    assert all(r.status == 200 for r in guard.reports)
    with queryguard.track("after", report=[]) as after:
        _execute(watched, 1)
    assert _markers(after) == ["SELECT 1"]  # no request scope left behind


# --- WSGI (Flask, a bare application, a dispatcher) --------------------------


def _flask_app(engine: Engine) -> Flask:
    app = Flask(__name__)

    def run(i: int, k: int) -> None:
        _execute(engine, 100 * i + k)

    @app.get("/endpoint/<int:i>")
    def endpoint(i: int) -> str:
        run(i, 1)
        return "ok"

    @app.get("/stream/<int:i>")
    def stream(i: int) -> Response:
        def body() -> Iterator[str]:
            for k in (1, 2):
                run(i, k)
                yield "part "

        return Response(stream_with_context(body()))

    @app.get("/stream-plain/<int:i>")
    def stream_plain(i: int) -> Response:
        def body() -> Iterator[str]:
            for k in (1, 2):
                run(i, k)
                yield "part "

        return Response(body())

    @app.get("/close/<int:i>")
    def close(i: int) -> Response:
        run(i, 1)
        response = Response("ok")
        response.call_on_close(lambda: run(i, 2))
        return response

    return app


WSGI_PHASES = {
    "endpoint": (1, b"ok"),
    "stream": (2, b"part part "),
    "stream-plain": (2, b"part part "),
    "close": (2, b"ok"),
}


def _wsgi_stack(engine: Engine, layout: str) -> tuple[Any, QueryGuardWSGI, str]:
    flask_app = _flask_app(engine)
    guard = QueryGuardWSGI(flask_app.wsgi_app, panel=True, report=[])
    if layout == "plain":
        return guard, guard, ""
    assert layout == "mounted"

    # Our middleware around an application mounted under SCRIPT_NAME /sub.
    def root(environ: dict[str, Any], start_response: Any) -> list[bytes]:
        start_response("404 Not Found", [])
        return [b""]

    return DispatcherMiddleware(root, {"/sub": guard}), guard, "/sub"


@pytest.mark.parametrize("layout", ["plain", "mounted"])
@pytest.mark.parametrize("phase", list(WSGI_PHASES))
def test_wsgi_requests_keep_their_statements(
    watched: Engine, phase: str, layout: str
) -> None:
    per_request, body = WSGI_PHASES[phase]
    app, guard, prefix = _wsgi_stack(watched, layout)
    client = WerkzeugClient(app)
    with queryguard.track("outer", report=[]) as outer:
        for i in range(1, REQUESTS + 1):
            response = client.get(f"{prefix}/{phase}/{i}")
            assert (response.status_code, response.data) == (200, body)
            response.close()
    _expect(guard.reports, per_request)
    # Each report is its own request's, application-relative (no SCRIPT_NAME).
    for report in guard.reports:
        i = int(_markers(report)[0].split()[1]) // 100
        assert report.path == f"/{phase}/{i}"
    assert _markers(outer) == []


def test_wsgi_interleaved_streamed_bodies_keep_their_statements(
    watched: Engine,
) -> None:
    """A server that reads several bodies in turn (one thread, many sockets).

    A plain generator: Flask's own ``stream_with_context`` cannot interleave
    (it pushes and pops Flask's request context per body).
    """
    phase = "stream-plain"
    flask_app = _flask_app(watched)
    guard = QueryGuardWSGI(flask_app.wsgi_app, panel=True, report=[])
    bodies = []
    with queryguard.track("outer", report=[]) as outer:
        for i in range(1, REQUESTS + 1):
            environ = {"REQUEST_METHOD": "GET", "PATH_INFO": f"/{phase}/{i}"}
            environ.update(
                {
                    "SERVER_NAME": "testserver",
                    "SERVER_PORT": "80",
                    "wsgi.url_scheme": "http",
                }
            )
            bodies.append(iter(guard(environ, lambda *a: None)))
        for _ in range(3):  # two parts and the end, a part of each in turn
            for body in bodies:
                next(body, None)
        for body in bodies:
            body.close()  # type: ignore[attr-defined]
    _expect(guard.reports, 2)
    assert _markers(outer) == []


def test_wsgi_head_request_is_measured_without_a_body(watched: Engine) -> None:
    guard = QueryGuardWSGI(_flask_app(watched).wsgi_app, panel=True, report=[])
    client = WerkzeugClient(guard)
    for i in range(1, REQUESTS + 1):
        response = client.head(f"/endpoint/{i}")
        assert (response.status_code, response.data) == (200, b"")
        response.close()
    _expect(guard.reports, 1)
    assert {r.method for r in guard.reports} == {"HEAD"}


def test_wsgi_error_half_way_through_a_body_ends_the_request(
    watched: Engine,
) -> None:
    flask_app = Flask(__name__)

    @flask_app.get("/broken/<int:i>")
    def broken(i: int) -> Response:
        def body() -> Iterator[str]:
            _execute(watched, 100 * i + 1)
            yield "part "
            raise RuntimeError("body bug")

        return Response(body())

    guard = QueryGuardWSGI(flask_app.wsgi_app, panel=True, report=[])
    client = WerkzeugClient(guard)
    for i in range(1, REQUESTS + 1):
        with pytest.raises(RuntimeError, match="body bug"):
            client.get(f"/broken/{i}", buffered=True)
    _expect(guard.reports, 1)
    with queryguard.track("after", report=[]) as after:
        _execute(watched, 1)
    assert _markers(after) == ["SELECT 1"]


# --- track(): a unit that spans a generator ----------------------------------


def test_track_generators_consumed_in_turn_keep_their_statements(
    watched: Engine,
) -> None:
    """The consumer's code between items is not part of the generator's unit."""
    seen: list[RequestReport] = []

    @queryguard.track(report=[seen.append], default_budget=0)
    def rows(i: int) -> Iterator[int]:
        for k in (1, 2):
            _execute(watched, 100 * i + k)
            yield k

    with queryguard.track("outer", report=[]) as outer:
        generators = [rows(i) for i in range(1, REQUESTS + 1)]
        for _ in range(3):  # two items and the end, one of each in turn
            for generator in generators:
                next(generator, None)
                _execute(watched, 9)  # the consumer's own statement
    _expect(tuple(seen), 2)
    assert _markers(outer) == ["SELECT 9"] * 3 * REQUESTS


def test_track_generator_closed_early_ends_its_own_unit(watched: Engine) -> None:
    seen: list[RequestReport] = []

    @queryguard.track("rows", report=[seen.append], default_budget=0)
    def rows(i: int) -> Iterator[int]:
        try:
            _execute(watched, 100 * i + 1)
            yield 1
            yield 2
        finally:
            _execute(watched, 100 * i + 2)

    first, second = rows(1), rows(2)
    next(first)
    next(second)
    first.close()  # ends the first unit, not the second one started after it
    second.close()
    with queryguard.track("after", report=[]) as after:
        _execute(watched, 1)
    assert sorted(_markers(r) for r in seen) == [
        ["SELECT 101", "SELECT 102"],
        ["SELECT 201", "SELECT 202"],
    ]
    assert _markers(after) == ["SELECT 1"]


def test_track_async_generators_consumed_in_turn_keep_their_statements(
    watched: Engine,
) -> None:
    seen: list[RequestReport] = []

    @queryguard.track(report=[seen.append], default_budget=0)
    async def rows(i: int) -> AsyncIterator[int]:
        for k in (1, 2):
            _execute(watched, 100 * i + k)
            await asyncio.sleep(0)
            yield k

    async def main() -> list[str]:
        generators = [rows(i) for i in range(1, REQUESTS + 1)]
        with queryguard.track("outer", report=[]) as outer:
            for _ in range(3):
                for generator in generators:
                    await anext(generator, None)
                    _execute(watched, 9)
        return _markers(outer)

    assert asyncio.run(main()) == ["SELECT 9"] * 3 * REQUESTS
    _expect(tuple(seen), 2)


def test_track_generator_send_and_throw_run_in_its_unit(watched: Engine) -> None:
    seen: list[RequestReport] = []

    @queryguard.track("echo", report=[seen.append], default_budget=0)
    def echo() -> Iterator[int]:
        value = yield 0
        while True:
            try:
                _execute(watched, value)
                value = yield value
            except KeyError:
                _execute(watched, 999)
                value = yield -1

    generator = echo()
    assert next(generator) == 0
    assert generator.send(101) == 101
    assert generator.throw(KeyError("x")) == -1
    assert generator.send(102) == 102
    generator.close()
    assert [_markers(r) for r in seen] == [["SELECT 101", "SELECT 999", "SELECT 102"]]


def test_track_generator_warning_is_raised_when_it_ends(watched: Engine) -> None:
    @queryguard.track("rows", report="warn", default_budget=0)
    def rows() -> Iterator[int]:
        _execute(watched, 101)
        yield 1

    with pytest.warns(queryguard.QueryGuardAppWarning):
        assert list(rows()) == [1]


def test_track_async_generator_closed_early_ends_its_own_unit(
    watched: Engine,
) -> None:
    seen: list[RequestReport] = []

    @queryguard.track("rows", report=[seen.append], default_budget=0)
    async def rows(i: int) -> AsyncIterator[int]:
        try:
            _execute(watched, 100 * i + 1)
            yield 1
            yield 2
        finally:
            _execute(watched, 100 * i + 2)

    async def main() -> None:
        first, second = rows(1), rows(2)
        await anext(first)
        await anext(second)
        await first.aclose()
        await second.aclose()

    asyncio.run(main())
    with queryguard.track("after", report=[]) as after:
        _execute(watched, 1)
    assert sorted(_markers(r) for r in seen) == [
        ["SELECT 101", "SELECT 102"],
        ["SELECT 201", "SELECT 202"],
    ]
    assert _markers(after) == ["SELECT 1"]


def test_track_unit_entered_inside_a_generator_spans_its_yields(
    watched: Engine,
) -> None:
    """A ``with track()`` across a yield stays the generator's current unit."""
    seen: list[RequestReport] = []

    @queryguard.track("rows", report=[seen.append], default_budget=0)
    def rows() -> Iterator[int]:
        _execute(watched, 101)
        with queryguard.track("batch", report=[seen.append], default_budget=0):
            _execute(watched, 201)
            yield 1
            _execute(watched, 202)
            yield 2
        _execute(watched, 102)
        yield 3

    with queryguard.track("outer", report=[]) as outer:
        for _ in rows():
            _execute(watched, 9)
    assert {r.name: _markers(r) for r in seen} == {
        "batch": ["SELECT 201", "SELECT 202"],
        "rows": ["SELECT 101", "SELECT 102"],
    }
    assert _markers(outer) == ["SELECT 9"] * 3


def test_track_unit_entered_inside_an_async_generator_spans_its_yields(
    watched: Engine,
) -> None:
    seen: list[RequestReport] = []

    @queryguard.track("rows", report=[seen.append], default_budget=0)
    async def rows() -> AsyncIterator[int]:
        _execute(watched, 101)
        async with queryguard.track("batch", report=[seen.append], default_budget=0):
            _execute(watched, 201)
            yield 1
            await asyncio.sleep(0)
            _execute(watched, 202)
            yield 2
        _execute(watched, 102)
        yield 3

    async def main() -> list[str]:
        with queryguard.track("outer", report=[]) as outer:
            async for _ in rows():
                _execute(watched, 9)
        return _markers(outer)

    assert asyncio.run(main()) == ["SELECT 9"] * 3
    assert {r.name: _markers(r) for r in seen} == {
        "batch": ["SELECT 201", "SELECT 202"],
        "rows": ["SELECT 101", "SELECT 102"],
    }
