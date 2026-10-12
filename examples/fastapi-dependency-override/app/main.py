from collections.abc import AsyncIterator, Iterator
from typing import Annotated

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, joinedload, selectinload

from app.db import async_sessions, sessions
from app.models import Order

app = FastAPI()


def get_session() -> Iterator[Session]:
    with sessions() as session:
        yield session


async def get_async_session() -> AsyncIterator[AsyncSession]:
    async with async_sessions() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
AsyncSessionDep = Annotated[AsyncSession, Depends(get_async_session)]


@app.get("/orders")
def list_orders(session: SessionDep) -> list[str]:
    return [order.customer.name for order in session.scalars(select(Order))]


@app.get("/orders-fixed")
def list_orders_fixed(session: SessionDep) -> list[str]:
    stmt = select(Order).options(joinedload(Order.customer))
    return [order.customer.name for order in session.scalars(stmt)]


@app.get("/async-orders")
async def list_async_orders(
    session: AsyncSessionDep,
) -> list[str]:
    names = []
    for order in (await session.scalars(select(Order))).all():
        names.append((await order.awaitable_attrs.customer).name)
    return names


@app.get("/async-orders-fixed")
async def list_async_orders_fixed(
    session: AsyncSessionDep,
) -> list[str]:
    stmt = select(Order).options(selectinload(Order.customer))
    return [order.customer.name for order in (await session.scalars(stmt)).all()]
