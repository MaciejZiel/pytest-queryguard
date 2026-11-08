"""CLEAN: a factory looks a row up for every object it creates, in the test body.

From Faraday (``tests/factories.py``): the user factory's ``post_generation``
hook looks the role up by name for each user it creates, so a test that
creates six users runs the same SELECT six times. That loop is in the
factory, test code that never runs in production, and the code under test
does not take part in it. With the default ``queryguard_test_code = off``
findings whose statements all ran in test code are left out; the test body
and the factory are in ``testside/clean_138_factory_lookup_per_object.py``.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN

VERDICT = CLEAN
KINDS = frozenset()
TEST_SIDE = "clean_138_factory_lookup_per_object"
setup = shop.setup


def product_names(session: Session) -> list[str]:
    """The code under test: one query."""
    return list(session.scalars(select(shop.Product.name).order_by(shop.Product.id)))
