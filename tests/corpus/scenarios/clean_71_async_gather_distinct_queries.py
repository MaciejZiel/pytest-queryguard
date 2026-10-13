"""CLEAN: a dashboard that gathers five different queries concurrently.

Each coroutine has its own ``AsyncSession`` and runs a different statement, so
nothing repeats: concurrency, not a loop.
"""

import asyncio

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


async def run(ctx: Ctx) -> None:
    sessions = async_sessionmaker(ctx.async_engine())

    async def scalar(stmt: object) -> object:
        async with sessions() as session:
            return await session.scalar(stmt)

    counts = await asyncio.gather(
        scalar(select(func.count()).select_from(shop.Customer)),
        scalar(select(func.count()).select_from(shop.Order)),
        scalar(select(func.sum(shop.Order.total))),
        scalar(select(func.max(shop.Product.price))),
        scalar(select(func.count()).select_from(shop.Tag)),
    )
    assert counts[:2] == [8, 24]
