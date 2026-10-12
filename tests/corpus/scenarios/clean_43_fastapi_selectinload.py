"""CLEAN: a FastAPI endpoint with ``selectinload`` and a Pydantic response model."""

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


class LineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    qty: int


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    lines: list[LineOut]


def make_app(ctx: Ctx) -> FastAPI:
    app = FastAPI()

    def get_session():  # type: ignore[no-untyped-def]
        with Session(ctx.engine()) as session:
            yield session

    @app.get("/orders", response_model=list[OrderOut])
    def orders(session: Session = Depends(get_session)) -> list[shop.Order]:
        stmt = select(shop.Order).options(selectinload(shop.Order.lines))
        return list(session.scalars(stmt))

    return app


def run(ctx: Ctx) -> None:
    with TestClient(make_app(ctx)) as client:
        response = client.get("/orders")
    assert response.status_code == 200 and len(response.json()) == 24
