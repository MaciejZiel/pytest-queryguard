"""BUG repeated-statement: a loop calls a store method that opens a Session per call.

From MLflow 2.22.0 (``test_log_batch_limits`` calls ``get_metric_history``
for each of 1000 metric keys): the store opens a Session per method, and a
report builds its table by calling the store once per product. One SELECT per
product, each in a Session of its own; one ``IN`` query for all the products
replaces them. Unlike ``clean_134_store_methods_at_separate_lines``, every
call comes from the same line, so the Sessions add up.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
KNOWN = {
    "session-scope": "by design: with queryguard_scope = session each Session is counted on its own, and each runs one lookup"
}
setup = shop.setup


class SalesStore:
    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx

    def quantities(self, product_id: int) -> list[int]:
        with Session(self.ctx.engine()) as session:
            return list(
                session.scalars(
                    select(shop.OrderLine.qty).where(
                        shop.OrderLine.product_id == product_id
                    )
                )
            )


def sales_report(store: SalesStore, product_ids: list[int]) -> dict[int, int]:
    report = {}
    for product_id in product_ids:
        report[product_id] = sum(store.quantities(product_id))
    return report


def run(ctx: Ctx) -> None:
    report = sales_report(SalesStore(ctx), list(range(1, 11)))
    assert len(report) == 10
