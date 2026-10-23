"""WSGI iterator setup and cleanup belong to the request being measured."""

from collections.abc import Iterator
from typing import Any

import pytest
from flask import Flask, Response
from sqlalchemy import text
from sqlalchemy.engine import Engine

from pytest_queryguard import app as queryguard


def _execute(engine: Engine, value: int) -> None:
    with engine.connect() as connection:
        connection.execute(text(f"SELECT {value}")).scalar_one()


def test_wsgi_iter_initialization_stays_in_the_request(watched: Engine) -> None:
    class Body:
        def __iter__(self) -> Iterator[bytes]:
            # A regular __iter__, not a generator: this runs during iter(body).
            _execute(watched, 102)
            return iter([b"ok"])

    def bare(environ: dict[str, Any], start_response: Any) -> Body:
        start_response("200 OK", [])
        _execute(watched, 101)
        return Body()

    app = queryguard.QueryGuardWSGI(bare, panel=True, report=[])
    with queryguard.track("outer", report=[]) as outer:
        body = app({"REQUEST_METHOD": "GET", "PATH_INFO": "/"}, lambda *a: None)
        assert b"".join(body) == b"ok"
        body.close()  # type: ignore[attr-defined]

    actual = {
        "request": [q.sql for q in app.reports[0].statements],
        "outer": [q.sql for q in outer.statements],
    }
    assert actual == {"request": ["SELECT 101", "SELECT 102"], "outer": []}


@pytest.mark.parametrize("buffered", [False, True])
def test_flask_close_callback_is_counted_after_body_consumption(
    watched: Engine,
    buffered: bool,
) -> None:
    flask_app = Flask(__name__)
    emitted: list[queryguard.RequestReport] = []
    closed: list[bool] = []
    middleware = queryguard.QueryGuardWSGI(
        flask_app.wsgi_app, panel=True, default_budget=1, report=emitted.append
    )
    flask_app.wsgi_app = middleware  # type: ignore[method-assign]

    @flask_app.get("/")
    def view() -> Response:
        _execute(watched, 201)
        response = Response("ok")

        @response.call_on_close
        def record_completion() -> None:
            _execute(watched, 202)
            closed.append(True)

        return response

    response = flask_app.test_client().get("/", buffered=buffered)
    assert response.get_data() == b"ok"
    response.close()
    response.close()
    assert closed == [True]
    report = middleware.reports[0]
    assert [q.sql for q in report.statements] == ["SELECT 201", "SELECT 202"]
    assert report.statement_count == 2
    assert report.over_budget
    assert emitted == [report]


@pytest.mark.parametrize("consume_first", [False, True])
def test_closing_early_runs_cleanup_once_and_stops_iteration(
    watched: Engine,
    consume_first: bool,
) -> None:
    closed: list[bool] = []

    class Body:
        def __iter__(self) -> Iterator[bytes]:
            _execute(watched, 301)
            yield b"first"
            _execute(watched, 302)
            yield b"last"

        def close(self) -> None:
            _execute(watched, 303)
            closed.append(True)

    def bare(environ: dict[str, Any], start_response: Any) -> Body:
        start_response("200 OK", [])
        return Body()

    app = queryguard.QueryGuardWSGI(bare, panel=True, report=[])
    with queryguard.track("outer", report=[]) as outer:
        body = app({"REQUEST_METHOD": "GET", "PATH_INFO": "/"}, lambda *a: None)
        iterator = iter(body)
        if consume_first:
            assert next(iterator) == b"first"
        body.close()  # type: ignore[attr-defined]
        body.close()  # type: ignore[attr-defined]
        with pytest.raises(StopIteration):
            next(iterator)
        _execute(watched, 304)

    assert closed == [True]
    assert len(app.reports) == 1
    assert [q.sql for q in app.reports[0].statements] == (
        ["SELECT 301", "SELECT 303"] if consume_first else ["SELECT 303"]
    )
    assert [q.sql for q in outer.statements] == ["SELECT 304"]


@pytest.mark.parametrize("failure", ["iter", "next", "close"])
def test_body_errors_restore_the_outer_context_and_close_once(
    watched: Engine,
    failure: str,
) -> None:
    closed: list[bool] = []

    class Body:
        def __iter__(self) -> Iterator[bytes]:
            _execute(watched, 401)
            if failure == "iter":
                raise RuntimeError("iter failed")
            return self

        def __next__(self) -> bytes:
            _execute(watched, 402)
            if failure == "next":
                raise RuntimeError("next failed")
            raise StopIteration

        def close(self) -> None:
            closed.append(True)
            _execute(watched, 403)
            if failure == "close":
                raise RuntimeError("close failed")

    def bare(environ: dict[str, Any], start_response: Any) -> Body:
        start_response("200 OK", [])
        return Body()

    app = queryguard.QueryGuardWSGI(bare, panel=True, report=[])
    with queryguard.track("outer", report=[]) as outer:
        body = app({"REQUEST_METHOD": "GET", "PATH_INFO": "/"}, lambda *a: None)
        with pytest.raises(RuntimeError, match=f"{failure} failed"):
            list(body)
        _execute(watched, 404)
        # A WSGI server still closes the response after iteration fails.
        body.close()  # type: ignore[attr-defined]
        body.close()  # type: ignore[attr-defined]
        _execute(watched, 405)

    assert closed == [True]
    assert len(app.reports) == 1
    assert [q.sql for q in app.reports[0].statements] == (
        ["SELECT 401", "SELECT 403"]
        if failure == "iter"
        else ["SELECT 401", "SELECT 402", "SELECT 403"]
    )
    assert [q.sql for q in outer.statements] == ["SELECT 404", "SELECT 405"]
