"""BUG repeated-statement: a three-iteration async ``get`` loop, below the threshold."""

from sqlalchemy.ext.asyncio import AsyncSession

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
KNOWN = {
    "*": "the repeat threshold is 5; queryguard_small_repeat_threshold catches it",
    "small-repeat": None,
    "all-opt-in": None,
}
setup = shop.setup


async def run(ctx: Ctx) -> None:
    async with AsyncSession(ctx.async_engine()) as session:
        found = [await session.get(shop.Product, i) for i in (1, 2, 3)]
    assert all(found)
