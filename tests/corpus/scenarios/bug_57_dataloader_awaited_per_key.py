"""BUG repeated-statement: a DataLoader that is awaited key by key.

The loader batches the keys that are queued in one event-loop tick, but the
resolver awaits each ``load()`` before it queues the next, so every batch has
one key and runs one query. Gathering the loads lets the loader batch them.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
setup = shop.setup


class DataLoader:
    """A minimal DataLoader: queued keys are loaded together on the next tick."""

    def __init__(self, batch: Callable[[Sequence[int]], Awaitable[list[object]]]):
        self.batch = batch
        self.queue: list[tuple[int, asyncio.Future[object]]] = []

    def load(self, key: int) -> asyncio.Future[object]:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[object] = loop.create_future()
        if not self.queue:
            loop.call_soon(lambda: asyncio.ensure_future(self._dispatch()))
        self.queue.append((key, future))
        return future

    async def _dispatch(self) -> None:
        queue, self.queue = self.queue, []
        values = await self.batch([key for key, _ in queue])
        for (_, future), value in zip(queue, values, strict=True):
            future.set_result(value)


async def run(ctx: Ctx) -> None:
    async with AsyncSession(ctx.async_engine()) as session:

        async def batch_products(ids: Sequence[int]) -> list[object]:
            rows = await session.scalars(
                select(shop.Product).where(shop.Product.id.in_(ids))
            )
            by_id = {p.id: p for p in rows}
            return [by_id[i] for i in ids]

        loader = DataLoader(batch_products)
        names = []
        for product_id in range(1, 11):
            product = await loader.load(product_id)
            names.append(product.name)
    assert len(names) == 10
