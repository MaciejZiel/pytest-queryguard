"""CLEAN: ``run_sync`` runs one function that uses an eager load."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, selectinload

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def totals(session: Session) -> list[int]:
    stmt = select(shop.Customer).options(selectinload(shop.Customer.orders))
    return [sum(o.total for o in c.orders) for c in session.scalars(stmt)]


async def run(ctx: Ctx) -> None:
    async with AsyncSession(ctx.async_engine()) as session:
        result = await session.run_sync(totals)
    assert len(result) == 8
