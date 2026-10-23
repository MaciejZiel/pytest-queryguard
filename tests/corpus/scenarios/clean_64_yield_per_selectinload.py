"""CLEAN: ``yield_per(200)`` with ``selectinload``: one eager load per batch.

With ``yield_per`` the ORM runs the ``selectinload`` for each batch of 200
orders as the batch is fetched, so 1200 orders give six ``IN`` statements. That
is the documented way to stream rows with their collections.

On SQLAlchemy 2.0.20 to 2.0.49 this also checks that the plugin's listeners do
not trigger SQLAlchemy issue 13301 (issue 169).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()


def setup(ctx: Ctx) -> None:
    shop.setup(ctx, customers=400, orders=3, lines=1, products=2, tags=1)


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        stmt = (
            select(shop.Order)
            .options(selectinload(shop.Order.lines))
            .execution_options(yield_per=200)
        )
        lines = sum(len(order.lines) for order in session.scalars(stmt))
    assert lines == 1200
