"""The development panel: off by default, JSON and a self-contained page."""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from flask import Flask
from sqlalchemy import literal_column, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from pytest_queryguard.app import QueryGuardASGI, QueryGuardWSGI
from pytest_queryguard.app._panel import HEADERS, allowed, html_body, is_loopback

from .conftest import Order


def _fastapi(engine: Engine) -> FastAPI:
    app = FastAPI()

    @app.get("/orders")
    def orders() -> list[str]:
        with Session(engine) as s:
            return [o.customer.name for o in s.scalars(select(Order))]

    @app.get("/evil")
    def evil() -> str:
        with Session(engine) as s:
            s.execute(text("SELECT '<script>alert(1)</script>'"))
        return "ok"

    return app


@pytest.fixture
def asgi_client(watched: Engine) -> Iterator[TestClient]:
    app = QueryGuardASGI(_fastapi(watched), panel=True, panel_history=3, report=[])
    with TestClient(app) as client:
        yield client


def test_the_panel_is_off_by_default(watched: Engine) -> None:
    app = QueryGuardASGI(_fastapi(watched), report=[])
    with TestClient(app) as client:
        assert client.get("/_queryguard/").status_code == 404  # the app's own 404
        assert client.get("/_queryguard/requests.json").status_code == 404
    assert app.reports == ()


def test_the_json_lists_the_last_requests_newest_first(asgi_client: TestClient) -> None:
    asgi_client.get("/orders")
    asgi_client.get("/evil")
    data = asgi_client.get("/_queryguard/requests.json").json()
    assert data["watched_engines"] == 1
    assert data["internal_error"] is None
    names = [r["name"] for r in data["requests"]]
    assert names == ["GET /evil", "GET /orders"]  # the panel's own are not kept
    orders = data["requests"][1]
    assert orders["statement_count"] == 6
    assert orders["status"] == 200
    assert orders["findings"][0]["label"] == "Order.customer"
    first = orders["statements"][0]
    assert first["sql"].startswith("SELECT orders.id")
    assert first["file"] == "tests/inapp/test_panel.py"
    assert first["duration"] > 0
    assert "parameters" not in first  # bound values never reach the panel


def test_the_history_keeps_the_last_n(asgi_client: TestClient) -> None:
    for _ in range(5):
        asgi_client.get("/evil")
    assert len(asgi_client.get("/_queryguard/requests.json").json()["requests"]) == 3


def test_the_page_is_self_contained(asgi_client: TestClient) -> None:
    response = asgi_client.get("/_queryguard/")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    page = response.text
    assert "Development only" in page
    assert not re.search(r"<script[^>]+src|<link|@import|https?://", page)
    assert '"/_queryguard/requests.json"' in page
    assert ".innerHTML" not in page  # values are rendered with textContent
    for name, value in HEADERS:
        assert response.headers[name] == value
    assert asgi_client.get("/_queryguard").status_code == 200


def test_the_panel_answers_get_and_head_on_known_paths(asgi_client: TestClient) -> None:
    assert asgi_client.post("/_queryguard/requests.json").status_code == 405
    assert asgi_client.get("/_queryguard/other").status_code == 404
    head = asgi_client.head("/_queryguard/requests.json")
    assert head.status_code == 200
    assert head.content == b""


def test_the_panel_refuses_other_clients_by_default(watched: Engine) -> None:
    app = QueryGuardASGI(_fastapi(watched), panel=True, report=[])
    with TestClient(app, client=("203.0.113.9", 4000)) as client:
        assert client.get("/_queryguard/requests.json").status_code == 403
    open_app = QueryGuardASGI(
        _fastapi(watched), panel=True, panel_loopback_only=False, report=[]
    )
    with TestClient(open_app, client=("203.0.113.9", 4000)) as client:
        assert client.get("/_queryguard/requests.json").status_code == 200


@pytest.mark.parametrize(
    ("host", "status"),
    [
        ("attacker.example:8000", 403),
        ("testserver.attacker.example", 403),
        ("testserver", 200),
        ("localhost.attacker.example", 403),
        ("localhost:8000", 200),
        ("LOCALHOST.", 200),
        ("app.localhost:8000", 200),
        ("127.0.0.1:8000", 200),
        ("[::1]:8000", 200),
    ],
)
def test_the_panel_refuses_a_rebound_host_name(
    watched: Engine, host: str, status: int
) -> None:
    # DNS rebinding: a page on attacker.example, its name now resolving to
    # 127.0.0.1, reads the panel as a same-origin URL from a loopback client.
    app = QueryGuardASGI(_fastapi(watched), panel=True, report=[])
    with TestClient(app, client=("127.0.0.1", 4000)) as client:
        response = client.get("/_queryguard/requests.json", headers={"host": host})
    assert response.status_code == status
    flask_app = Flask(__name__)
    wsgi = QueryGuardWSGI(flask_app.wsgi_app, panel=True, report=[])
    flask_app.wsgi_app = wsgi  # type: ignore[method-assign]
    response = flask_app.test_client().get(
        "/_queryguard/requests.json", headers={"Host": host}
    )
    assert response.status_code == status


def test_a_custom_path_and_root_path(watched: Engine) -> None:
    app = QueryGuardASGI(_fastapi(watched), panel=True, panel_path="/debug/qg/")
    with TestClient(app, root_path="/api") as client:
        page = client.get("/debug/qg").text
    assert '"/api/debug/qg/requests.json"' in page


def test_the_json_url_is_escaped() -> None:
    page = html_body('/x"><script>alert(1)</script>').decode()
    assert "<script>alert(1)" not in page
    assert "</script>" not in page.split("const url = ", 1)[1].split(";", 1)[0]
    assert 'const url = "/x\\"\\u003e\\u003cscript\\u003e' in page


def test_the_json_url_keeps_an_ampersand() -> None:
    # A root_path or SCRIPT_NAME with "&" must reach fetch() unchanged.
    page = html_body("/a&b/_queryguard/requests.json").decode()
    assert 'const url = "/a&b/_queryguard/requests.json";' in page


@pytest.mark.parametrize("path", ["", "/", "relative", None])
def test_a_bad_panel_path_is_refused(path: object) -> None:
    with pytest.raises(ValueError, match="panel_path"):
        QueryGuardASGI(lambda *a: None, panel=True, panel_path=path)  # type: ignore[arg-type]


def test_a_bad_history_size_is_refused() -> None:
    with pytest.raises(ValueError, match="panel_history"):
        QueryGuardWSGI(lambda *a: [], panel=True, panel_history=0)


def test_the_wsgi_panel(watched: Engine) -> None:
    flask_app = Flask(__name__)

    @flask_app.get("/n")
    def n() -> str:
        with Session(watched) as s:
            s.execute(select(literal_column("5")))
        return "ok"

    middleware = QueryGuardWSGI(flask_app.wsgi_app, panel=True, report=[])
    flask_app.wsgi_app = middleware  # type: ignore[method-assign]
    client = flask_app.test_client()
    assert client.get("/n").text == "ok"  # read: exhausted, then closed
    assert len(middleware.reports) == 1  # finished once, not twice
    data = client.get("/_queryguard/requests.json").get_json()
    assert [r["statement_count"] for r in data["requests"]] == [1]
    page = client.get("/_queryguard/")
    assert page.status_code == 200
    assert page.headers["Content-Security-Policy"].startswith("default-src 'none'")
    other = client.get(
        "/_queryguard/requests.json", environ_base={"REMOTE_ADDR": "203.0.113.9"}
    )
    assert other.status_code == 403
    assert client.post("/_queryguard/").status_code == 405


@pytest.mark.parametrize(
    ("host", "loopback"),
    [
        ("127.0.0.1", True),
        ("127.0.0.42", True),
        ("::1", True),
        ("::ffff:127.0.0.1", True),
        ("testclient", True),
        ("10.0.0.1", False),
        ("::ffff:10.0.0.1", False),
        ("2001:db8::1", False),
        ("example.org", False),
    ],
)
def test_loopback_clients(host: str, loopback: bool) -> None:
    assert is_loopback(host) is loopback
    assert allowed(host, loopback_only=True) is loopback
    assert allowed(host, loopback_only=False)
    # A Unix socket peer has no address; behind a proxy it may be anyone.
    assert not allowed(None, loopback_only=True)
    assert allowed(None, loopback_only=False)
