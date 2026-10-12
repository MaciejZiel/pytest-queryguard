"""CLEAN: a retry loop that polls a job status until it is done.

The same SELECT six times, on purpose: another worker finishes the job while the
loop waits (simulated by an UPDATE on a second connection). Real code would
sleep between polls. Tests of such code need ``allow_nplusone``.
"""

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
KNOWN = {
    "*": "a legitimate repeat of one statement is indistinguishable from a loop; use allow_nplusone",
}
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session, Session(ctx.engine()) as worker:
        for attempt in range(1, 7):
            status = session.scalar(select(shop.Order.status).where(shop.Order.id == 1))
            if status == "done":
                break
            if attempt == 5:
                worker.execute(
                    update(shop.Order).where(shop.Order.id == 1).values(status="done")
                )
                worker.commit()
    assert status == "done"
