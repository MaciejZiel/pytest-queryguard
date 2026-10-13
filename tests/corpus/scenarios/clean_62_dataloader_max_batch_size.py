"""CLEAN: a DataLoader with ``max_batch_size=100`` resolving 650 keys.

The resolver gathers every load, so the loader batches them, and splits the
batch into chunks of 100 as DataLoaders do: seven ``IN`` statements that
partition one list of ids.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()


def setup(ctx: Ctx) -> None:
    shop.setup(ctx, customers=1, orders=1, lines=1, products=650, tags=1)


class DataLoader:
    """A minimal DataLoader with a maximum batch size."""

    def __init__(
        self,
        batch: Callable[[Sequence[int]], Awaitable[list[object]]],
        max_batch_size: int,
    ) -> None:
        self.batch = batch
        self.max_batch_size = max_batch_size
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
        for start in range(0, len(queue), self.max_batch_size):
            chunk = queue[start : start + self.max_batch_size]
            values = await self.batch([key for key, _ in chunk])
            for (_, future), value in zip(chunk, values, strict=True):
                future.set_result(value)


async def run(ctx: Ctx) -> None:
    async with AsyncSession(ctx.async_engine()) as session:

        async def prices(ids: Sequence[int]) -> list[object]:
            rows = await session.execute(
                select(shop.Product.id, shop.Product.price).where(
                    shop.Product.id.in_(ids)
                )
            )
            found = dict(rows.tuples().all())
            return [found[i] for i in ids]

        loader = DataLoader(prices, max_batch_size=100)
        values = await asyncio.gather(*(loader.load(i) for i in range(1, 651)))
    assert len(values) == 650
