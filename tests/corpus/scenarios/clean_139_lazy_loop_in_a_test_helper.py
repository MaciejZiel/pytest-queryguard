"""CLEAN: a helper of the test reads relationships lazily; the code under test does not.

The test builds its expected values with a helper of its own that loops over
the customers and reads each one's orders lazily: an N+1 in test code, in the
test body this time (``clean_97`` has it in a fixture). The code under test
uses ``selectinload``. With the default ``queryguard_test_code = off`` the
helper's lazy loads are left out; they are in
``testside/clean_139_lazy_loop_in_a_test_helper.py``.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..domains import shop
from ..kit import CLEAN

VERDICT = CLEAN
KINDS = frozenset()
TEST_SIDE = "clean_139_lazy_loop_in_a_test_helper"
setup = shop.setup


def order_counts(session: Session) -> dict[str, int]:
    """The code under test: the orders come with their customers."""
    stmt = select(shop.Customer).options(selectinload(shop.Customer.orders))
    return {c.name: len(c.orders) for c in session.scalars(stmt)}
