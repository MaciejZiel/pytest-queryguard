"""CLEAN: one handler called from six lines of the test, in one shared Session.

From Faraday's API tests: a test sends several requests one after the other
through the test client, and the application's Session is shared by all of
them (the app context of the suite is pushed once). Each request looks its
workspace up and reads the customer of one order: six requests run the
lookup six times and lazy-load six customers, once per request. No line of
the application or the test runs it twice; ``queryguard_scope = session``
cannot tell the requests apart, as they share the Session. The count rules
count the statements of a call path of their own one by one, and the lazy
loads of separate test lines one by one. The test body is in
``testside/clean_140_requests_at_separate_lines.py``.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN

VERDICT = CLEAN
KINDS = frozenset()
KNOWN = {
    "strict": "strict mode raises at the lazy load, before later loads show "
    "which test lines call the application again, so it counts the lazy "
    "loads of all test lines together",
}
TEST_SIDE = "clean_140_requests_at_separate_lines"
setup = shop.setup


def order_page(session: Session, order_id: int) -> dict[str, object]:
    """The request handler: a lookup, then the order with its customer's name."""
    category = session.scalars(
        select(shop.Category).where(shop.Category.name == "cat1")
    ).one()
    order = session.get(shop.Order, order_id)
    assert order is not None
    return {"id": order.id, "customer": order.customer.name, "category": category.id}
