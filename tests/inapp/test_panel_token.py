"""The panel token: required off loopback, checked on requests.json only."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from flask import Flask

from pytest_queryguard.app import QueryGuardASGI, QueryGuardWSGI
from pytest_queryguard.app._panel import HEADERS, authorized, check_token, html_body

TOKEN = "s3cret-token_0123456789"
REMOTE = ("203.0.113.9", 4000)
JSON = "/_queryguard/requests.json"
Get = Callable[..., tuple[int, dict[str, str], bytes]]


def _asgi_get(**options: Any) -> Get:
    app = QueryGuardASGI(FastAPI(), panel=True, report=[], **options)

    def get(
        path: str, client: tuple[str, int] = REMOTE, method: str = "GET", **kw: Any
    ) -> tuple[int, dict[str, str], bytes]:
        with TestClient(app, client=client) as c:
            r = c.request(method, path, **kw)
        return r.status_code, dict(r.headers), r.content

    return get


def _wsgi_get(**options: Any) -> Get:
    flask_app = Flask(__name__)
    middleware = QueryGuardWSGI(flask_app.wsgi_app, panel=True, report=[], **options)
    flask_app.wsgi_app = middleware  # type: ignore[method-assign]
    test_client = flask_app.test_client()

    def get(
        path: str, client: tuple[str, int] = REMOTE, method: str = "GET", **kw: Any
    ) -> tuple[int, dict[str, str], bytes]:
        environ = {"REMOTE_ADDR": client[0]}
        r = test_client.open(path, method=method, environ_base=environ, **kw)
        return r.status_code, dict(r.headers), r.data

    return get


@pytest.fixture(params=["asgi", "wsgi"])
def make(request: pytest.FixtureRequest) -> Callable[..., Get]:
    return _asgi_get if request.param == "asgi" else _wsgi_get


def _bearer(token: str) -> dict[str, dict[str, str]]:
    return {"headers": {"Authorization": f"Bearer {token}"}}


def _lower(headers: dict[str, str]) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items()}


@pytest.mark.parametrize("middleware", [QueryGuardASGI, QueryGuardWSGI])
def test_an_open_panel_needs_a_token(middleware: type) -> None:
    with pytest.raises(ValueError, match=r"panel_token.*secrets\.token_urlsafe"):
        middleware(lambda *a: None, panel=True, panel_loopback_only=False)
    # Without the panel, or on loopback only, there is nothing to protect.
    middleware(lambda *a: None, panel_loopback_only=False)
    middleware(lambda *a: None, panel=True)


@pytest.mark.parametrize(
    "token", ["", "short", "x" * 15, "x" * 16 + " ", "ł" * 16, "x" * 16 + "\n"]
)
def test_a_weak_or_odd_token_is_refused(token: str) -> None:
    with pytest.raises(ValueError, match="at least 16 characters") as info:
        QueryGuardASGI(FastAPI(), panel=True, panel_token=token)
    if token:
        assert token not in str(info.value)  # a secret stays out of messages


@pytest.mark.parametrize("token", [b"x" * 16, 12345678901234567, ["x" * 16]])
def test_a_token_must_be_a_string(token: object) -> None:
    with pytest.raises(TypeError, match="panel_token must be a string"):
        QueryGuardWSGI(lambda *a: [], panel=True, panel_token=token)  # type: ignore[arg-type]


def test_token_forms() -> None:
    # The secrets module's alphabets and base64 padding are accepted.
    assert check_token("Ab0-_.~+/" * 2 + "==", True, False) == b"Ab0-_.~+/" * 2 + b"=="
    assert check_token(None, True, True) is None
    assert authorized(None, None)
    good = TOKEN.encode()
    assert authorized(good, f"Bearer {TOKEN}")
    assert authorized(good, f"bearer  {TOKEN} ")
    assert not authorized(good, None)
    assert not authorized(good, TOKEN)
    assert not authorized(good, f"Basic {TOKEN}")
    assert authorized(good, f"Bearer\t{TOKEN}")
    assert not authorized(good, "Bearer")
    assert not authorized(good, "Bearer ")
    assert not authorized(good, f"Bearer {TOKEN}x")
    assert not authorized(good, "Bearer " + TOKEN.upper())
    assert not authorized(good, "Bearer \xe9" + TOKEN[1:])  # latin-1, no error


def test_the_json_needs_the_token(make: Callable[..., Get]) -> None:
    get = make(panel_loopback_only=False, panel_token=TOKEN)
    for kwargs in ({}, _bearer("wrong-token-0123456789"), _bearer(TOKEN[:-1])):
        status, headers, body = get(JSON, **kwargs)
        assert status == 401
        headers = _lower(headers)
        assert headers["www-authenticate"] == "Bearer"
        assert headers["content-type"] == "text/plain; charset=utf-8"
        for name, value in HEADERS:  # no-store, CSP and the rest stay
            assert headers[name] == value
        assert body == b"missing or wrong panel token"
        assert b"requests" not in body
    status, headers, body = get(JSON, **_bearer(TOKEN))
    assert status == 200
    assert b'"requests": []' in body
    assert "www-authenticate" not in _lower(headers)
    head = get(JSON, method="HEAD")
    assert (head[0], head[2]) == (401, b"")


def test_the_page_needs_no_token(make: Callable[..., Get]) -> None:
    get = make(panel_loopback_only=False, panel_token=TOKEN)
    status, _, body = get("/_queryguard/")
    assert status == 200
    assert TOKEN.encode() not in body  # the page holds no secret and no data
    assert get("/_queryguard/other")[0] == 404


def test_the_order_of_checks(make: Callable[..., Get]) -> None:
    get = make(panel_token=TOKEN)  # loopback only and a token: both apply
    assert get(JSON, method="POST")[0] == 405  # the method first
    assert get(JSON, **_bearer(TOKEN))[0] == 403  # then the client
    assert (
        get(JSON, client=("127.0.0.1", 4000), headers={"Host": "localhost"})[0] == 401
    )
    status, _, _ = get(
        JSON,
        client=("127.0.0.1", 4000),
        headers={"Host": "localhost", "Authorization": f"Bearer {TOKEN}"},
    )
    assert status == 200


def test_the_page_sends_the_fragment_token() -> None:
    page = html_body("/_queryguard/requests.json").decode()
    assert "location.hash" in page
    assert '"hashchange", load' in page  # a pasted token loads without a reload
    assert '"Authorization": "Bearer " + t' in page
    assert "location.search" not in page  # never from the query string
    assert "res.status === 401" in page
    assert "stopAuto();" in page  # no polling with a refused token
    assert "#token=" in page
