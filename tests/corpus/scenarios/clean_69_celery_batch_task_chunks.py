"""CLEAN: a Celery-style task per chunk of 200 order ids, 1200 ids in total.

The producer splits the ids into chunks and enqueues one task per chunk; each
task (run eagerly, as in tests) loads its chunk with one ``IN`` query in its own
session. Six statements of one shape whose ``IN`` lists partition the ids.
"""

from collections.abc import Callable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
CHUNK = 200


def setup(ctx: Ctx) -> None:
    shop.setup(ctx, customers=400, orders=3, lines=1, products=2, tags=1)


class EagerTask:
    """The part of a Celery task that matters here: ``delay`` runs it now."""

    def __init__(self, fn: Callable[..., object]) -> None:
        self.fn = fn

    def delay(self, *args: object) -> object:
        return self.fn(*args)


def run(ctx: Ctx) -> None:
    exported: list[int] = []

    @EagerTask
    def export_orders(order_ids: Sequence[int]) -> None:
        with Session(ctx.engine()) as session:
            totals = session.scalars(
                select(shop.Order.total).where(shop.Order.id.in_(order_ids))
            ).all()
            exported.extend(totals)

    ids = list(range(1, 1201))
    for start in range(0, len(ids), CHUNK):
        export_orders.delay(ids[start : start + CHUNK])
    assert len(exported) == 1200
