"""BUG repeated-statement: a query per row inside ``async for`` over a stream."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
setup = shop.setup


async def run(ctx: Ctx) -> None:
    sold = []
    async with AsyncSession(ctx.async_engine()) as outer, AsyncSession(ctx.async_engine()) as inner:
        async for product in await outer.stream_scalars(select(shop.Product)):
            count = await inner.scalar(
                select(func.count()).where(shop.OrderLine.product_id == product.id)
            )
            sold.append(count)
    assert len(sold) == 12
