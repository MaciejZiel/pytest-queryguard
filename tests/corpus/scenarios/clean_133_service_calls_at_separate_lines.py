"""CLEAN: a service that opens a Session per call, called at separate lines.

From MLflow 2.22.0 (``tests/store/tracking/test_sqlalchemy_store.py``,
``test_set_experiment_tag``), seen by ``.github/workflows/realworld.yml``: each
method of ``SqlAlchemyStore`` opens its own Session, loads the experiment with
``_get_experiment()`` and reads its tags. The test calls the store on several
lines, for two experiments, with no loop: each call is a unit of work of its
own, and each loads one experiment.

The parents are grouped by the line of the query that loaded them, here the
one line of the shared helper, and by default the whole test is one unit, so
the two experiments looked like one query returning two rows. Each Session now
carries the lines that called it when it began; a Session begun from lines no
other Session of the test began from is counted on its own. A loop over the
helper begins every Session from the same lines (``bug_47_session_per_item``).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def _get_customer(session: Session, customer_id: int) -> shop.Customer:
    return session.scalars(
        select(shop.Customer).where(shop.Customer.id == customer_id)
    ).one()


def order_count(ctx: Ctx, customer_id: int) -> int:
    with Session(ctx.engine()) as session:
        return len(_get_customer(session, customer_id).orders)


def run(ctx: Ctx) -> None:
    first = order_count(ctx, 1)
    second = order_count(ctx, 2)
    assert first > 0 and second > 0
