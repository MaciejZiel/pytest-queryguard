"""CLEAN: a statistics function runs one count per status, each on a line of its own.

From Faraday (``faraday/server/debouncer.py``): the workspace statistics are
counted with one helper, called once per severity on six separate lines, so
the same statement shape runs six times with other literals. That is a fixed
number of queries whatever the data, not a loop over rows: no line runs it
twice. It could be one ``GROUP BY``, but it does not grow with the rows, so
the repeat rule counts a statement whose call path no other run has on its own.
"""

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def order_stats(session: Session) -> dict[str, int]:
    def count(extra: str) -> int:
        stmt = select(func.count(shop.Order.id)).where(text(extra))
        return int(session.scalar(stmt) or 0)

    return {
        "new": count("status = 'new'"),
        "paid": count("status = 'paid'"),
        "sent": count("status = 'sent'"),
        "returned": count("status = 'returned'"),
        "lost": count("status = 'lost'"),
        "closed": count("status = 'closed'"),
    }


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        assert order_stats(session)["new"] == 24
