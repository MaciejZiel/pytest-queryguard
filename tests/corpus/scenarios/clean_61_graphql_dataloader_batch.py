"""CLEAN: a GraphQL-style resolver backed by a DataLoader, one query for all parents.

The executor resolves ``Customer.openOrders`` for every customer; the resolver
only queues the customer id, and the loader runs one ``IN`` query for the keys
queued in that tick. The fix of ``bug_51_graphql_field_resolver``.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


class DataLoader:
    """A minimal DataLoader: keys queued in one tick are loaded together."""

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

        async def open_orders(customer_ids: Sequence[int]) -> list[object]:
            rows = await session.execute(
                select(shop.Order.customer_id, shop.Order.id).where(
                    shop.Order.customer_id.in_(customer_ids),
                    shop.Order.status == "new",
                )
            )
            found: dict[int, list[int]] = {}
            for customer_id, order_id in rows:
                found.setdefault(customer_id, []).append(order_id)
            return [found.get(i, []) for i in customer_ids]

        loader = DataLoader(open_orders)
        customers = (await session.scalars(select(shop.Customer))).all()
        data = await asyncio.gather(*(loader.load(c.id) for c in customers))
    assert len(data) == 8 and all(len(ids) == 3 for ids in data)
