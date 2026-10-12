from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import StaticPool

from app.models import Base, Customer, Order

engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
sessions = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as session:
        session.add_all(Customer(id=i, name=f"customer {i}") for i in range(1, 6))
        session.add_all(Order(id=i, customer_id=i) for i in range(1, 6))
        await session.commit()
    yield
    await engine.dispose()


app = FastAPI(lifespan=lifespan)


@app.get("/orders")
async def list_orders() -> list[str]:
    async with sessions() as session:
        orders = (await session.scalars(select(Order))).all()
        return [(await order.awaitable_attrs.customer).name for order in orders]


@app.get("/orders-fixed")
async def list_orders_fixed() -> list[str]:
    async with sessions() as session:
        stmt = select(Order).options(selectinload(Order.customer))
        orders = (await session.scalars(stmt)).all()
        return [order.customer.name for order in orders]
