"""CLEAN: the repository takes all order ids and runs one ``IN`` query."""

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


class OrderRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def all(self) -> list[shop.Order]:
        return list(self.session.scalars(select(shop.Order)))

    def lines_for(self, order_ids: list[int]) -> dict[int, list[shop.OrderLine]]:
        stmt = select(shop.OrderLine).where(shop.OrderLine.order_id.in_(order_ids))
        grouped: dict[int, list[shop.OrderLine]] = defaultdict(list)
        for line in self.session.scalars(stmt):
            grouped[line.order_id].append(line)
        return grouped


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        repo = OrderRepository(session)
        orders = repo.all()
        lines = repo.lines_for([o.id for o in orders])
        totals = {o.id: sum(line.qty for line in lines[o.id]) for o in orders}
    assert len(totals) == 24
