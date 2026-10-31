"""report="warn" under an "error" filter never replaces the app's exception."""

from __future__ import annotations

import asyncio
import logging
import warnings
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from pytest_queryguard import QueryGuardWarning
from pytest_queryguard import app as queryguard
from pytest_queryguard.app import QueryGuardASGI, QueryGuardWSGI


class AppError(Exception):
    pass


@pytest.fixture
def warnings_are_errors() -> Iterator[None]:
    with warnings.catch_warnings():
        warnings.simplefilter("error", QueryGuardWarning)
        yield


def _query(engine: Engine) -> None:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1")).scalar()


def _asgi(engine: Engine, fail: bool) -> QueryGuardASGI:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        _query(engine)  # over the budget of 0
        if fail:
            raise AppError("from the app")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    return QueryGuardASGI(app, default_budget=0, report="warn")


def _call_asgi(app: QueryGuardASGI) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []
    scope = {"type": "http", "method": "GET", "path": "/", "headers": []}

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b""}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return sent


def _wsgi(engine: Engine, fail: str | None) -> QueryGuardWSGI:
    def body() -> Iterator[bytes]:
        _query(engine)
        if fail == "body":
            raise AppError("from the body")
        yield b"ok"

    def app(environ: Any, start_response: Any) -> Iterator[bytes]:
        if fail == "call":
            _query(engine)
            raise AppError("from the app")
        start_response("200 OK", [])
        return body()

    return QueryGuardWSGI(app, default_budget=0, report="warn")


def _call_wsgi(app: QueryGuardWSGI) -> bytes:
    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": "/"}
    result = app(environ, lambda status, headers, exc_info=None: None)
    try:
        return b"".join(result)
    finally:
        close = getattr(result, "close", None)
        if close is not None:
            close()


@pytest.mark.usefixtures("warnings_are_errors")
def test_asgi_keeps_the_apps_exception(watched: Engine) -> None:
    with pytest.raises(AppError, match="from the app"):
        _call_asgi(_asgi(watched, fail=True))


@pytest.mark.usefixtures("warnings_are_errors")
def test_asgi_raises_the_warning_after_a_successful_response(watched: Engine) -> None:
    with pytest.raises(QueryGuardWarning, match="expected at most 0 queries"):
        _call_asgi(_asgi(watched, fail=False))


@pytest.mark.usefixtures("warnings_are_errors")
@pytest.mark.parametrize("where", ["call", "body"])
def test_wsgi_keeps_the_apps_exception(watched: Engine, where: str) -> None:
    with pytest.raises(AppError):
        _call_wsgi(_wsgi(watched, fail=where))


@pytest.mark.usefixtures("warnings_are_errors")
def test_wsgi_logs_the_error_instead_of_cutting_the_body_short(
    watched: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    # Raised from the body, the error would stop the server before the last
    # chunk of a chunked response (gunicorn, waitress); it cannot fail a
    # response that has started, so it is logged at ERROR instead.
    app = _wsgi(watched, fail=None)
    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": "/"}
    result = app(environ, lambda status, headers, exc_info=None: None)
    with caplog.at_level(logging.ERROR, logger="pytest_queryguard.app"):
        assert list(result) == [b"ok"]
        result.close()  # the server's close() raises nothing either
    (record,) = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert "expected at most 0 queries" in record.getMessage()
    assert "QueryGuardWarning" in record.getMessage()


@pytest.mark.usefixtures("warnings_are_errors")
def test_wsgi_keeps_the_servers_error_when_the_client_disconnects(
    watched: Engine,
) -> None:
    # wsgiref and gunicorn close the body inside their handler of the write
    # error; the warning must not replace it.
    app = _wsgi(watched, fail=None)
    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": "/"}
    result = app(environ, lambda status, headers, exc_info=None: None)
    with pytest.raises(BrokenPipeError):
        try:
            for _chunk in result:
                raise BrokenPipeError("the client went away")
        except BaseException:
            result.close()
            raise
    result.close()  # a later close() raises nothing either


@pytest.mark.usefixtures("warnings_are_errors")
def test_track_keeps_the_blocks_exception(watched: Engine) -> None:
    with (
        pytest.raises(AppError),
        queryguard.track(default_budget=0, report="warn"),
    ):
        _query(watched)
        raise AppError("from the block")


@pytest.mark.usefixtures("warnings_are_errors")
def test_track_raises_the_warning_after_a_successful_block(watched: Engine) -> None:
    with (
        pytest.raises(QueryGuardWarning),
        queryguard.track(default_budget=0, report="warn"),
    ):
        _query(watched)


def test_other_targets_still_run_when_the_warning_is_an_error(
    watched: Engine,
) -> None:
    reports: list[Any] = []
    with warnings.catch_warnings():
        warnings.simplefilter("error", QueryGuardWarning)
        with (
            pytest.raises(QueryGuardWarning),
            queryguard.track(default_budget=0, report=["warn", reports.append]),
        ):
            _query(watched)
    assert len(reports) == 1


def test_every_report_warns_and_the_registry_does_not_grow(watched: Engine) -> None:
    # A long-running server: the "default" action must not hide repeats, and
    # Python's per-module warning registry must not keep each message.
    from pytest_queryguard.app import _core

    registry = vars(_core).setdefault("__warningregistry__", {})
    before = len(registry)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("default")
        for _ in range(3):
            with queryguard.track("same", default_budget=0, report="warn"):
                _query(watched)
    assert len(caught) == 3
    assert all(issubclass(w.category, QueryGuardWarning) for w in caught)
    assert len(registry) == before
