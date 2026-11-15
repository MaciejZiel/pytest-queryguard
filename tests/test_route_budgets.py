"""Route budgets in pytest: queryguard_route_budgets and its marker."""

from __future__ import annotations

import pytest

from pytest_queryguard import routes

# pytester runs sessions in-process, so the outer plugin sees their queries too.
pytestmark = pytest.mark.allow_nplusone

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

STARLETTE_APP = """
from fastapi import BackgroundTasks, FastAPI
from fastapi.responses import StreamingResponse
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

engine = create_engine(
    "sqlite://",
    poolclass=StaticPool,
    connect_args={"check_same_thread": False},
)
app = FastAPI()


def run(n):
    with engine.connect() as conn:
        for _ in range(n):
            conn.execute(text("select 1"))


@app.get("/books/{book_id}")
async def book(book_id: int, n: int = 3):
    run(n)
    return {"id": book_id}


@app.get("/authors")
def authors(n: int = 2):  # a sync endpoint: in Starlette's thread pool
    run(n)
    return []


@app.get("/stream")
def stream(n: int = 2):
    def parts():
        for _ in range(n):
            run(1)
            yield b"x"

    return StreamingResponse(parts())


@app.get("/later")
def later(tasks: BackgroundTasks, n: int = 2):
    tasks.add_task(run, n)
    return {}


sub = FastAPI()


@sub.get("/items/{item_id}")
def item(item_id: int, n: int = 2):
    run(n)
    return {}


app.mount("/sub", sub)
"""


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makepyfile(starapp=STARLETTE_APP)
    return pytester


def _ini(pytester: pytest.Pytester, budgets: str, default: str = "") -> None:
    lines = "\n            ".join(budgets.strip().splitlines())
    pytester.makeini(
        f"""
        [pytest]
        queryguard_route_budgets =
            {lines}
        queryguard_route_default_budget = {default}
        """
    )


def _test(pytester: pytest.Pytester, body: str, head: str = "") -> None:
    pytester.makepyfile(
        test_routes=f"""
import pytest
from fastapi.testclient import TestClient
from starapp import app, run

pytestmark = pytest.mark.allow_nplusone
client = TestClient(app)
{head}
def test_requests():
{body}
"""
    )


def test_a_request_over_its_route_budget_fails_the_test(
    project: pytest.Pytester,
) -> None:
    _ini(project, "GET /books/{book_id}=2")
    _test(project, "    client.get('/books/1')")
    result = project.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            (
                "*queryguard: route budget: GET /books/{book_id} may run at most 2 "
                "statements per request, GET /books/1 ran 3"
            ),
            '*(the limit is "GET /books/{book_id}" in queryguard_route_budgets)',
            "*1. select 1  (starapp.py:17 in run)",
            "*3. select 1  (starapp.py:17 in run)",
        ]
    )


def test_each_request_counts_on_its_own(project: pytest.Pytester) -> None:
    _ini(project, "GET /books/*=3")
    # 9 statements in all, 3 per request; the test's own do not count
    _test(
        project,
        "    run(5)\n"
        "    for i in range(3):\n"
        "        assert client.get(f'/books/{i}').status_code == 200",
    )
    project.runpytest().assert_outcomes(passed=1)


def test_several_requests_over_are_reported_once_per_route(
    project: pytest.Pytester,
) -> None:
    _ini(project, "/books/{book_id}=1")
    _test(
        project,
        "    client.get('/books/1?n=2')\n"
        "    client.get('/books/2?n=4')\n"
        "    client.get('/books/3?n=1')",
    )
    result = project.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            (
                "*route budget: GET /books/{book_id} may run at most 1 statement per "
                "request, GET /books/2 ran 4"
            ),
            "*2 requests of this route went over; the one with the most statements:",
        ]
    )
    assert result.stdout.str().count("may run at most") == 1


def test_the_default_budget_covers_other_routes(project: pytest.Pytester) -> None:
    _ini(project, "GET /books/{book_id}=5", default="1")
    _test(project, "    client.get('/books/1')\n    client.get('/authors')")
    result = project.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            (
                "*route budget: GET /authors may run at most 1 statement per request, "
                "GET /authors ran 2"
            ),
            "*(the limit is queryguard_route_default_budget)",
        ]
    )
    assert "GET /books" not in result.stdout.str()


def test_the_marker_comes_before_the_ini_file(project: pytest.Pytester) -> None:
    _ini(project, "GET /books/{book_id}=1", default="0")
    _test(
        project,
        "    client.get('/books/1')\n    client.get('/authors')",
        head="\n@pytest.mark.queryguard_route_budgets("
        "{'GET /books/*': 3}, default=None)",
    )
    project.runpytest().assert_outcomes(passed=1)


def test_the_marker_alone_and_its_source(project: pytest.Pytester) -> None:
    _test(
        project,
        "    client.get('/authors')",
        head="\n@pytest.mark.queryguard_route_budgets({'/authors': 1})",
    )
    result = project.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        ['*(the limit is "/authors" in the queryguard_route_budgets marker)']
    )


@pytest.mark.parametrize(
    ("marker", "error"),
    [
        ("['GET /a']", "needs a mapping of route patterns to limits"),
        ("{'GET /a': -1}", "the limit of 'GET /a' must be an integer >= 0"),
        ("{'GET /a': 1}, limit=2", "takes a mapping of route patterns"),
        ("{'GET /a': 1}, default='x'", "default must be an integer >= 0"),
    ],
)
def test_a_bad_marker_fails_the_test(
    project: pytest.Pytester, marker: str, error: str
) -> None:
    _test(
        project,
        "    pass",
        head=f"\n@pytest.mark.queryguard_route_budgets({marker})",
    )
    result = project.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines([f"*{error}*"])


@pytest.mark.parametrize(
    ("budgets", "default", "error"),
    [
        ("GET /a", "", "is not a route budget: write it as PATTERN=LIMIT"),
        ("GET /a=x", "", "the limit of 'GET /a' must be an integer >= 0"),
        ("GET /a=1", "-2", "queryguard_route_default_budget must be an integer"),
    ],
)
def test_a_bad_ini_value_is_a_usage_error(
    project: pytest.Pytester, budgets: str, default: str, error: str
) -> None:
    _ini(project, budgets, default)
    _test(project, "    pass")
    result = project.runpytest()
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines([f"*{error}*"])


def test_a_streamed_body_and_background_tasks_count(project: pytest.Pytester) -> None:
    _ini(project, "GET /stream=2\nGET /later=2")
    _test(
        project,
        "    assert client.get('/stream?n=3').content == b'xxx'\n"
        "    client.get('/later?n=4')",
    )
    result = project.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            (
                "*route budget: GET /stream may run at most 2 statements per request, "
                "GET /stream ran 3"
            ),
            (
                "*route budget: GET /later may run at most 2 statements per request, "
                "GET /later ran 4"
            ),
        ]
    )


def test_a_mounted_application_has_its_mount_prefix(
    project: pytest.Pytester,
) -> None:
    _ini(project, "GET /sub/items/{item_id}=1")
    _test(project, "    client.get('/sub/items/7')")
    result = project.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            (
                "*route budget: GET /sub/items/{item_id} may run at most 1 statement "
                "per request, GET /sub/items/7 ran 2"
            )
        ]
    )
    assert result.stdout.str().count("may run at most") == 1


def test_an_async_client_in_the_test_loop(project: pytest.Pytester) -> None:
    _ini(project, "GET /books/{book_id}=2")
    project.makepyfile(
        test_routes="""
import asyncio

import httpx
from starapp import app


def test_requests():
    async def main():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            await c.get("/books/1")

    asyncio.run(main())
"""
    )
    result = project.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*GET /books/1 ran 3"])


def test_without_route_budgets_nothing_is_measured(project: pytest.Pytester) -> None:
    _test(project, "    client.get('/books/1?n=50')")
    project.runpytest().assert_outcomes(passed=1)


def test_a_failing_test_keeps_its_own_failure(project: pytest.Pytester) -> None:
    _ini(project, "GET /books/{book_id}=1")
    _test(project, "    client.get('/books/1')\n    assert False, 'own failure'")
    result = project.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*own failure*"])
    assert "route budget:" not in result.stdout.str()


def test_works_with_measurement_off(project: pytest.Pytester) -> None:
    _ini(project, "GET /books/{book_id}=1")
    _test(project, "    client.get('/books/1')")
    result = project.runpytest("--queryguard-mode=off")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*GET /books/1 ran 3"])


FLASK_APP = """
from flask import Flask, Response, send_file, stream_with_context
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

engine = create_engine(
    "sqlite://",
    poolclass=StaticPool,
    connect_args={"check_same_thread": False},
)
app = Flask(__name__)


def run(n):
    with engine.connect() as conn:
        for _ in range(n):
            conn.execute(text("select 1"))


@app.get("/books/<int:book_id>")
def book(book_id):
    run(3)
    return {"id": book_id}


@app.get("/stream")
def stream():
    def parts():
        for _ in range(3):
            run(1)
            yield "x"

    response = Response(parts())
    response.call_on_close(lambda: run(1))
    return response


@app.get("/file")
def file():
    run(1)
    return send_file(__file__)
"""


def test_flask(pytester: pytest.Pytester) -> None:
    pytest.importorskip("flask")
    pytester.makepyfile(flaskapp=FLASK_APP)
    _ini(pytester, "GET /books/<int:book_id>=2\nGET /stream=3\nGET /file=1")
    pytester.makepyfile(
        test_routes="""
from flaskapp import app

def test_requests():
    client = app.test_client()
    assert client.get("/books/1").status_code == 200
    assert client.get("/stream").data == b"xxx"
    assert client.get("/file").data.startswith(b"from flask")
"""
    )
    result = pytester.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            (
                "*route budget: GET /books/<int:book_id> may run at most 2 statements "
                "per request, GET /books/1 ran 3"
            ),
            (
                "*route budget: GET /stream may run at most 3 statements per request, "
                "GET /stream ran 4"
            ),
        ]
    )
    assert "GET /file" not in result.stdout.str()


LITESTAR_APP = """
from litestar import Litestar, get
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

engine = create_engine(
    "sqlite://",
    poolclass=StaticPool,
    connect_args={"check_same_thread": False},
)


@get("/books/{book_id:int}", sync_to_thread=False)
def book(book_id: int) -> dict:
    with engine.connect() as conn:
        for _ in range(3):
            conn.execute(text("select 1"))
    return {}


app = Litestar([book])
"""


def test_litestar(pytester: pytest.Pytester) -> None:
    pytest.importorskip("litestar")
    pytester.makepyfile(litapp=LITESTAR_APP)
    _ini(pytester, "GET /books/{book_id}=2")
    pytester.makepyfile(
        test_routes="""
from litestar.testing import TestClient
from litapp import app

def test_requests():
    with TestClient(app) as client:
        assert client.get("/books/1").status_code == 200
"""
    )
    result = pytester.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*GET /books/{book_id} may * GET /books/1 ran 3"])


@pytest.mark.parametrize(
    ("key", "method", "route", "path", "match"),
    [
        ("GET /books/{id}", "GET", "/books/{id}", "/books/1", True),
        ("get /books/{id}", "GET", "/books/{id}", "/books/1", True),
        ("POST /books/{id}", "GET", "/books/{id}", "/books/1", False),
        ("/books/*", "DELETE", None, "/books/1", True),
        ("/a b", "GET", None, "/a b", True),
        ("M-SEARCH /x", "M-SEARCH", None, "/x", True),
    ],
)
def test_matching(
    key: str, method: str, route: str | None, path: str, match: bool
) -> None:
    assert (routes.matching_key({key: 1}, method, route, path) == key) is match


def test_parse_lines() -> None:
    assert routes.parse_lines(["GET /a=1", "", "# note", " /b/*= 2 ", "GET /a=7"]) == {
        "GET /a": 1,
        "/b/*": 2,
    }
