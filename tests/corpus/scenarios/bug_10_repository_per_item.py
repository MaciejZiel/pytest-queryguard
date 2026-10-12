"""BUG repeated-statement: a repository method called once per order.

``InvoiceService.build`` asks ``OrderRepository.lines_for(order_id)`` inside a
loop over all orders: the same SELECT with a different id, 24 times. The loop
and the query live in different classes, as in layered code.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
setup = shop.setup


class OrderRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def all(self) -> list[shop.Order]:
        return list(self.session.scalars(select(shop.Order)))

    def lines_for(self, order_id: int) -> list[shop.OrderLine]:
        stmt = select(shop.OrderLine).where(shop.OrderLine.order_id == order_id)
        return list(self.session.scalars(stmt))


class InvoiceService:
    def __init__(self, repo: OrderRepository) -> None:
        self.repo = repo

    def build(self) -> dict[int, int]:
        return {
            o.id: sum(line.qty for line in self.repo.lines_for(o.id))
            for o in self.repo.all()
        }


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        totals = InvoiceService(OrderRepository(session)).build()
    assert len(totals) == 24
