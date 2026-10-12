"""CLEAN: ``awaitable_attrs`` on one object, not in a loop."""

from sqlalchemy.ext.asyncio import AsyncSession

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


async def run(ctx: Ctx) -> None:
    async with AsyncSession(ctx.async_engine()) as session:
        customer = await session.get(shop.Customer, 1)
        orders = await customer.awaitable_attrs.orders
    assert len(orders) == 3
