"""CLEAN: the test arranges its rows with a loop that calls a service each time.

From a Django project's suite (``services.create_league(user, ...)``
ten times in a ``for`` loop to reach the per-user limit, users joining a
league one by one): the service counts the customer's orders to enforce a
limit, then adds one. Each call runs the count once, as each request does in
production; the loop that repeats it is the test's arrange step, so the
repetition is the test code's (``queryguard_test_code``). Before 1.1.0 it was
reported as an application ``repeated-statement`` and needed a marker. The
test body is in
``testside/clean_143_service_called_per_case_in_a_test_loop.py``.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN

VERDICT = CLEAN
KINDS = frozenset()
TEST_SIDE = "clean_143_service_called_per_case_in_a_test_loop"
setup = shop.setup

LIMIT = 8


class LimitReached(Exception):
    pass


def place_order(session: Session, customer_id: int, order_id: int) -> shop.Order:
    """The code under test: one count, then one insert."""
    placed = session.scalar(
        select(func.count(shop.Order.id)).where(shop.Order.customer_id == customer_id)
    )
    if placed >= LIMIT:
        raise LimitReached(customer_id)
    order = shop.Order(id=order_id, customer_id=customer_id, total=0)
    session.add(order)
    session.flush()
    return order
