"""BUG repeated-statement: a FastAPI endpoint calls ``session.get`` for each id.

``GET /products?ids=...`` fetches the products one by one, through a
dependency-provided session, under ``TestClient`` (the endpoint runs in a
worker thread).
"""

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
setup = shop.setup


def make_app(ctx: Ctx) -> FastAPI:
    app = FastAPI()

    def get_session():  # type: ignore[no-untyped-def]
        with Session(ctx.engine()) as session:
            yield session

    @app.get("/products")
    def products(
        ids: list[int] = Query(), session: Session = Depends(get_session)
    ) -> list[dict[str, object]]:
        found = [session.get(shop.Product, i) for i in ids]
        return [{"id": p.id, "name": p.name} for p in found if p is not None]

    return app


def run(ctx: Ctx) -> None:
    with TestClient(make_app(ctx)) as client:
        response = client.get("/products", params={"ids": list(range(1, 11))})
    assert response.status_code == 200 and len(response.json()) == 10
