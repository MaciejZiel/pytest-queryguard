"""QueryGuardWSGI with Flask and the Flask test client."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from flask import Flask, Response, request, stream_with_context
from sqlalchemy import literal_column, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, joinedload

from pytest_queryguard import QueryGuardWarning
from pytest_queryguard import app as queryguard
from pytest_queryguard.app import QueryGuardWSGI, RequestReport

from .conftest import CUSTOMERS, Order


def _flask_app(engine: Engine, **options: Any) -> tuple[Flask, list[RequestReport]]:
    app = Flask(__name__)
    reports: list[RequestReport] = []
    options.setdefault("report", reports.append)
    app.wsgi_app = QueryGuardWSGI(app.wsgi_app, **options)  # type: ignore[method-assign]

    @app.before_request
    def name_the_route() -> None:
        if request.url_rule is not None:
            queryguard.set_route(request.url_rule.rule)

    @app.get("/orders")
    def orders() -> list[str]:
        with Session(engine) as s:
            return [o.customer.name for o in s.scalars(select(Order))]

    @app.get("/orders-fixed")
    def orders_fixed() -> list[str]:
        with Session(engine) as s:
            query = select(Order).options(joinedload(Order.customer))
            return [o.customer.name for o in s.scalars(query)]

    @app.get("/orders/<int:order_id>")
    def one(order_id: int) -> str:
        with Session(engine) as s:
            return s.get_one(Order, order_id).customer.name

    @app.get("/stream")
    def stream() -> Response:
        def body() -> Iterator[str]:
            with Session(engine) as s:
                for n in range(5):
                    s.execute(select(literal_column("7"))).scalar()
                    yield f"{n}\n"

        return Response(stream_with_context(body()))

    @app.get("/count/<int:uid>")
    def count(uid: int) -> str:
        with Session(engine) as s:
            for step in range(uid % 7 + 1):
                s.execute(select(literal_column(str(1000 * (step + 1) + uid))))
        return "ok"

    @app.get("/boom")
    def boom() -> str:
        raise RuntimeError("view bug")

    return app, reports


def test_flask_test_client_reports_per_request(watched: Engine) -> None:
    app, reports = _flask_app(watched)
    client = app.test_client()
    # The request ends when its body is read or closed, as servers do.
    assert client.get("/orders").get_json()[0] == "customer 1"
    with client.get("/orders-fixed") as response:
        assert response.status_code == 200
    assert len(reports) == 1
    report = reports[0]
    assert (report.method, report.path, report.route, report.status) == (
        "GET",
        "/orders",
        "/orders",
        200,
    )
    assert report.statement_count == 1 + CUSTOMERS
    assert [f.label for f in report.findings] == ["Order.customer"]


def test_set_route_gives_budgets_the_rule(watched: Engine) -> None:
    app, reports = _flask_app(watched, budgets={"GET /orders/<int:order_id>": 1})
    app.test_client().get("/orders/10").close()
    report = reports[0]
    assert report.route == "/orders/<int:order_id>"
    assert report.budget == 1
    assert report.over_budget


def test_a_streamed_body_counts_until_it_is_closed(watched: Engine) -> None:
    app, reports = _flask_app(watched)
    response = app.test_client().get("/stream")
    assert response.data == b"0\n1\n2\n3\n4\n"
    assert [f.kind for f in reports[0].findings] == ["repeated-statement"]
    assert reports[0].statement_count == 5


def test_a_view_error_is_reported_and_propagates(watched: Engine) -> None:
    app, reports = _flask_app(watched, default_budget=0)
    app.testing = False  # Flask turns the error into a 500 response
    assert app.test_client().get("/boom").status_code == 500
    # No statements and a budget of 0: nothing to report, but the unit ended.
    assert reports == []
    assert queryguard.internal_error() is None


def test_the_warn_target_follows_the_warning_filters(watched: Engine) -> None:
    app, _ = _flask_app(watched, report="warn")
    with pytest.warns(QueryGuardWarning, match="Order.customer"):
        app.test_client().get("/orders", buffered=True)


def test_concurrent_flask_requests_in_threads(watched: Engine) -> None:
    app, reports = _flask_app(watched, default_budget=0)

    def get(uid: int) -> int:
        with app.test_client().get(f"/count/{uid}") as response:
            return response.status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert set(pool.map(get, range(50))) == {200}
    assert len(reports) == 50
    for report in reports:
        assert report.path is not None
        uid = int(report.path.rsplit("/", 1)[1])
        assert report.statement_count == uid % 7 + 1
        assert {q.sql.split(" AS ")[0] for q in report.statements} == {
            f"SELECT {1000 * (step + 1) + uid}" for step in range(uid % 7 + 1)
        }


def test_the_context_is_left_after_each_request(watched: Engine) -> None:
    app, _ = _flask_app(watched)
    app.test_client().get("/orders-fixed").close()
    with Session(watched) as s:  # outside any request now
        s.execute(select(literal_column("1")))
    with queryguard.track(report=[]) as report:
        pass
    assert report.statement_count == 0


def _environ(path: str = "/x") -> dict[str, Any]:
    return {"REQUEST_METHOD": "GET", "PATH_INFO": path}


def test_a_bare_wsgi_app_that_raises(watched: Engine) -> None:
    def bare(environ: dict[str, Any], start_response: Any) -> list[bytes]:
        with Session(watched) as s:
            s.execute(select(literal_column("1")))
        raise RuntimeError("app bug")

    reports: list[RequestReport] = []
    app = QueryGuardWSGI(bare, default_budget=0, report=reports.append)
    with pytest.raises(RuntimeError, match="app bug"):
        app(_environ(), lambda *a: None)
    assert reports[0].status == 500
    assert reports[0].statement_count == 1


def test_closing_an_unread_body_ends_the_request(watched: Engine) -> None:
    closed: list[bool] = []

    class Body:
        def __iter__(self) -> Iterator[bytes]:
            yield b"never read"

        def close(self) -> None:
            with Session(watched) as s:  # runs in the request's context
                s.execute(select(literal_column("2")))
            closed.append(True)

    def bare(environ: dict[str, Any], start_response: Any) -> Body:
        start_response("200 OK", [])
        return Body()

    reports: list[RequestReport] = []
    app = QueryGuardWSGI(bare, default_budget=0, report=reports.append)
    body = app(_environ(), lambda *a: None)
    assert reports == []
    body.close()  # type: ignore[attr-defined]
    assert closed == [True]
    assert reports[0].statement_count == 1
    assert reports[0].status == 200
