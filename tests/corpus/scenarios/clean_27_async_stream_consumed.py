"""CLEAN: ``stream_scalars`` over 24 orders, nothing else queried per row."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


async def run(ctx: Ctx) -> None:
    ids = []
    async with AsyncSession(ctx.async_engine()) as session:
        async for order in await session.stream_scalars(select(shop.Order)):
            ids.append(order.id)
    assert len(ids) == 24
