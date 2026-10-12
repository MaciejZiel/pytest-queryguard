"""BUG repeated-statement: ``AsyncSession.run_sync`` with a lookup per id."""

from sqlalchemy.ext.asyncio import AsyncSession

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
setup = shop.setup


async def run(ctx: Ctx) -> None:
    async with AsyncSession(ctx.async_engine()) as session:
        names = [
            await session.run_sync(lambda s, i=i: s.get(shop.Product, i).name)
            for i in range(1, 11)
        ]
    assert len(names) == 10
