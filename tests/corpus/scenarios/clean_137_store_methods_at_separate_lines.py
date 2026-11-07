"""CLEAN: six calls of a store that opens a Session per method, on six lines.

From MLflow 2.22.0 (``tests/store/tracking/test_sqlalchemy_store.py``): every
method of the store opens its own Session and starts with the same lookup
(``_get_experiment()``), and the tests set and read tags with one call per
line. The lookup runs six times in the test, once in each unit of work, and no
line runs it twice: there is nothing to batch. Six runs of one statement were
reported as a repeated statement by default, because the whole test was one
unit (``queryguard_scope = test``); a Session begun from lines no other
Session began from is now counted on its own.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


class CustomerStore:
    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx

    def _get_customer(self, session: Session, customer_id: int) -> shop.Customer:
        return session.scalars(
            select(shop.Customer).where(shop.Customer.id == customer_id)
        ).one()

    def rename(self, customer_id: int, name: str) -> None:
        with Session(self.ctx.engine()) as session:
            self._get_customer(session, customer_id).name = name
            session.commit()

    def name_of(self, customer_id: int) -> str:
        with Session(self.ctx.engine()) as session:
            return self._get_customer(session, customer_id).name


def run(ctx: Ctx) -> None:
    store = CustomerStore(ctx)
    store.rename(1, "Ada")
    store.rename(2, "Grace")
    assert store.name_of(1) == "Ada"
    assert store.name_of(2) == "Grace"
    store.rename(1, "Ada L.")
    assert store.name_of(1) == "Ada L."
