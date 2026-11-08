"""BUG relationship-load: the application loops lazily, called from a helper of the test.

The test calls the code under test through a helper of its own, as suites do
for requests (Faraday's test client lives in ``conftest.py``). The loop and
the lazy loads are in the application, so the finding is the application's,
although the outermost frames are test code. The test body and the helper
are in ``testside/bug_135_app_loop_called_from_a_test_helper.py``.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REL

VERDICT = BUG
KINDS = frozenset({REL})
PATCH = True
TEST_SIDE = "bug_135_app_loop_called_from_a_test_helper"
setup = shop.setup


def order_rows(session: Session) -> list[dict[str, object]]:
    """The code under test: one lazy load of the customer per order."""
    orders = session.scalars(select(shop.Order).order_by(shop.Order.id)).all()
    return [{"id": o.id, "customer": o.customer.name} for o in orders]
