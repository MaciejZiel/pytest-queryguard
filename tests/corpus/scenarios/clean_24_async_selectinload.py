"""CLEAN: ``selectinload`` on an AsyncSession, relationship read afterwards."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


async def run(ctx: Ctx) -> None:
    async with AsyncSession(ctx.async_engine()) as session:
        stmt = select(shop.Customer).options(selectinload(shop.Customer.orders))
        counts = [len(c.orders) for c in (await session.scalars(stmt)).all()]
    assert counts == [3] * 8
