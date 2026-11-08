"""BUG repeated-statement: one of several requests looks a product up per order line.

The test sends requests one per line through one shared Session, as in
``clean_140``; the requests made on separate lines are no loop. One handler
loops over the lines of an order and looks each product up with its own
query: that loop is in the application, and runs the lookup six times from
one call path. The test body is in
``testside/bug_136_lookup_loop_among_separate_requests.py``.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REPEAT

VERDICT = BUG
KINDS = frozenset({REPEAT})
TEST_SIDE = "bug_136_lookup_loop_among_separate_requests"
setup = shop.setup


def customer_page(session: Session, customer_id: int) -> str:
    customer = session.get(shop.Customer, customer_id)
    assert customer is not None
    return customer.name


def product_names(session: Session, ids: list[int]) -> list[str]:
    """The code under test: one query per product."""
    return [
        session.scalars(select(shop.Product.name).where(shop.Product.id == i)).one()
        for i in ids
    ]
