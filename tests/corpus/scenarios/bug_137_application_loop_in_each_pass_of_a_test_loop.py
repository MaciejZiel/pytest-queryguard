"""BUG relationship-load: a loop of the test calls a handler that loops itself.

As ``clean_142``, a loop of the test calls the application once per pass;
here each call lists two orders and reads each order's
customer lazily, so the application repeats the read inside one call. That
is the application's loop, whatever the test around it does. The test body
is in ``testside/bug_137_application_loop_in_each_pass_of_a_test_loop.py``.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REL

VERDICT = BUG
KINDS = frozenset({REL})
TEST_SIDE = "bug_137_application_loop_in_each_pass_of_a_test_loop"
setup = shop.setup


def order_names(session: Session, order_ids: list[int]) -> list[str]:
    """The code under test: the customer of each order, read one by one."""
    orders = session.scalars(select(shop.Order).where(shop.Order.id.in_(order_ids)))
    return [order.customer.name for order in orders]
