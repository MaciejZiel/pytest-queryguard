"""BUG repeated-statement: ``await session.get()`` for each id on an AsyncSession."""

from sqlalchemy.ext.asyncio import AsyncSession

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
setup = shop.setup


async def run(ctx: Ctx) -> None:
    async with AsyncSession(ctx.async_engine()) as session:
        found = [await session.get(shop.Product, i) for i in range(1, 11)]
    assert all(found)
