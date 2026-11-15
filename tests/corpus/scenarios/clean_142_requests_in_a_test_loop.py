"""CLEAN: one handler called once per pass of a loop of the test, in one Session.

From the "debatable" findings of the real-project runs (Faraday's paginated
requests in a ``for`` loop, the Django projects' tests that loop over cases):
the test calls the handler of ``clean_140`` in a loop instead of on six
lines. Each call looks its category up and reads the customer of its one
order: the application does the work once per call, as it does for each
request in production, and no call repeats it. The loop is the test's, so
the repetition is the test code's (``queryguard_test_code``). The test body
is in ``testside/clean_142_requests_in_a_test_loop.py``.
"""

from ..domains import shop
from ..kit import CLEAN
from .clean_140_requests_at_separate_lines import order_page

VERDICT = CLEAN
KINDS = frozenset()
KNOWN = {
    "strict": "strict mode raises at the lazy load, before later loads show "
    "that each call of the test's loop reads one customer",
}
TEST_SIDE = "clean_142_requests_in_a_test_loop"
setup = shop.setup

__all__ = ["order_page"]
