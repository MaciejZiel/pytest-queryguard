"""CLEAN: ``from_attributes`` models over relationships loaded eagerly."""

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    name: str


class LineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    qty: int


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    customer: CustomerOut
    lines: list[LineOut]


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        stmt = select(shop.Order).options(
            joinedload(shop.Order.customer), selectinload(shop.Order.lines)
        )
        out = [OrderOut.model_validate(o) for o in session.scalars(stmt).unique()]
    assert len(out) == 24
