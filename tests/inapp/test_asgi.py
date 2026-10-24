"""QueryGuardASGI with FastAPI (TestClient, httpx.AsyncClient) and bare ASGI."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import literal_column, select
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import Session, joinedload

from pytest_queryguard import app as queryguard
from pytest_queryguard.app import QueryGuardASGI, RequestReport

from .conftest import CUSTOMERS, Order


def _sync_app(engine: Engine) -> FastAPI:
    app = FastAPI()

    def session() -> Iterator[Session]:
        with Session(engine) as s:
            yield s

    @app.get("/orders")
    def orders(s: Session = Depends(session)) -> list[str]:  # noqa: B008
        return [o.customer.name for o in s.scalars(select(Order))]

    @app.get("/orders-fixed")
    def orders_fixed(s: Session = Depends(session)) -> list[str]:  # noqa: B008
        query = select(Order).options(joinedload(Order.customer))
        return [o.customer.name for o in s.scalars(query)]

    @app.get("/orders/{order_id}")
    def one(order_id: int, s: Session = Depends(session)) -> str:  # noqa: B008
        return s.get(Order, order_id).customer.name

    @app.get("/boom")
    def boom(s: Session = Depends(session)) -> None:  # noqa: B008
        s.execute(select(literal_column("1")))
        raise RuntimeError("endpoint bug")

    return app


def _async_app(engine: AsyncEngine) -> FastAPI:
    app = FastAPI()

    async def session() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(engine) as s:
            yield s

    @app.get("/orders")
    async def orders(s: AsyncSession = Depends(session)) -> list[str]:  # noqa: B008
        names = []
        for order in (await s.scalars(select(Order))).all():
            names.append((await order.awaitable_attrs.customer).name)
            await asyncio.sleep(0)
        return names

    @app.get("/count/{steps}/{uid}")
    async def count(steps: int, uid: int, s: AsyncSession = Depends(session)) -> int:  # noqa: B008
        for step in range(steps):
            await s.execute(select(literal_column(str(1000 * (step + 1) + uid))))
            await asyncio.sleep(0)
        return steps

    return app


def test_fastapi_test_client_reports_per_request(
    watched: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    reports: list[RequestReport] = []
    app = _sync_app(watched)
    app.add_middleware(QueryGuardASGI, report=[reports.append, "log"])
    with TestClient(app) as client:
        assert client.get("/orders").status_code == 200
        assert client.get("/orders-fixed").status_code == 200
    assert len(reports) == 1  # only the request with problems is reported
    report = reports[0]
    assert (report.method, report.path, report.route, report.status) == (
        "GET",
        "/orders",
        "/orders",
        200,
    )
    assert report.statement_count == 1 + CUSTOMERS
    assert [f.label for f in report.findings] == ["Order.customer"]
    assert report.findings[0].file == "tests/inapp/test_asgi.py"
    assert "queryguard: GET /orders: 6 statements" in caplog.text


def test_budgets_match_the_route_template(watched: Engine) -> None:
    reports: list[RequestReport] = []
    app = _sync_app(watched)
    app.add_middleware(
        QueryGuardASGI,
        budgets={"GET /orders/{order_id}": 1, "/orders*": 100},
        report=reports.append,
    )
    with TestClient(app) as client:
        client.get("/orders/10")
        client.get("/orders-fixed")
    assert len(reports) == 1
    report = reports[0]
    assert report.route == "/orders/{order_id}"
    assert report.path == "/orders/10"
    assert report.budget == 1
    assert report.statement_count == 2
    assert report.over_budget
    assert "(route /orders/{order_id})" in report.message
    assert "expected at most 1 query, got 2" in report.message


def test_an_endpoint_error_is_reported_and_propagates(watched: Engine) -> None:
    reports: list[RequestReport] = []
    app = _sync_app(watched)
    app.add_middleware(QueryGuardASGI, default_budget=0, report=reports.append)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/boom").status_code == 500
    assert reports[0].status == 500
    assert reports[0].statement_count == 1


def test_httpx_async_client_with_an_async_engine(async_engine: AsyncEngine) -> None:
    queryguard.watch(async_engine)
    reports: list[RequestReport] = []
    app = QueryGuardASGI(_async_app(async_engine), report=reports.append)

    async def main() -> None:
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                response = await client.get("/orders")
                assert response.status_code == 200
        finally:
            await async_engine.dispose()

    asyncio.run(main())
    assert [f.label for f in reports[0].findings] == ["Order.customer"]
    assert reports[0].statement_count == 1 + CUSTOMERS


def test_50_concurrent_requests_through_httpx(async_engine: AsyncEngine) -> None:
    queryguard.watch(async_engine)
    reports: list[RequestReport] = []
    # A budget every request exceeds, so every request is reported.
    app = QueryGuardASGI(
        _async_app(async_engine), default_budget=0, report=reports.append
    )

    async def main() -> None:
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                responses = await asyncio.gather(
                    *(client.get(f"/count/{n % 7 + 1}/{n}") for n in range(50))
                )
                assert all(r.status_code == 200 for r in responses)
        finally:
            await async_engine.dispose()

    asyncio.run(main())
    assert len(reports) == 50
    assert len({r.path for r in reports}) == 50
    for report in reports:
        assert report.path is not None
        steps, uid = (int(part) for part in report.path.split("/")[2:])
        assert report.statement_count == steps == len(report.statements)
        values = {q.sql.split(" AS ")[0] for q in report.statements}
        assert values == {f"SELECT {1000 * (step + 1) + uid}" for step in range(steps)}
        assert report.route == "/count/{steps}/{uid}"


def test_concurrent_sync_endpoints_in_the_thread_pool(watched: Engine) -> None:
    reports: list[RequestReport] = []
    app = QueryGuardASGI(_sync_app(watched), default_budget=0, report=reports.append)

    async def main() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            paths = [f"/orders/{c_id * 10}" for c_id in range(1, CUSTOMERS + 1)] * 6
            responses = await asyncio.gather(*(c.get(p) for p in paths))
            assert all(r.status_code == 200 for r in responses)

    asyncio.run(main())
    assert len(reports) == 6 * CUSTOMERS
    assert all(r.statement_count == 2 for r in reports)


def test_bare_asgi_app_and_other_scope_types(watched: Engine) -> None:
    seen: list[str] = []

    async def bare(scope: dict[str, Any], receive: Any, send: Any) -> None:
        seen.append(scope["type"])
        if scope["type"] != "http":
            return
        with Session(watched) as s:
            for _ in range(5):
                s.execute(select(literal_column("42")))
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    reports: list[RequestReport] = []
    app = QueryGuardASGI(bare, report=reports.append)

    async def main() -> None:
        await app({"type": "lifespan"}, _never, _drop)
        await app({"type": "http", "method": "GET", "path": "/x"}, _never, _drop)

    asyncio.run(main())
    assert seen == ["lifespan", "http"]
    assert reports[0].status == 204
    assert reports[0].route is None
    assert [f.kind for f in reports[0].findings] == ["repeated-statement"]


async def _never() -> dict[str, Any]:
    raise AssertionError("not called")


async def _drop(message: Any) -> None:
    return None


def test_an_internal_error_never_breaks_a_request(
    watched: Engine,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR, logger="pytest_queryguard.app")

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("detector bug")

    monkeypatch.setattr("pytest_queryguard.app._core.detect", broken)
    app = _sync_app(watched)
    app.add_middleware(QueryGuardASGI)
    with TestClient(app) as client:
        assert client.get("/orders").status_code == 200
        assert client.get("/orders").status_code == 200  # the mode is off now
    assert "in-app mode switched off" in caplog.text
    assert queryguard.internal_error() is not None
