"""CLEAN: a nested transaction around one batch, with a fallback on conflict.

``begin_nested()`` wraps the insert of a batch of tags; when the batch fails on
a duplicate, the savepoint is rolled back and the batch retried without the
known names. Two savepoints and two batches, no loop over rows.
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def run(ctx: Ctx) -> None:
    wanted = [{"id": i, "name": f"tag{i}"} for i in range(1, 21)]
    with Session(ctx.engine()) as session:
        try:
            with session.begin_nested():
                session.add_all(shop.Tag(**row) for row in wanted)
        except IntegrityError:
            known = set(session.scalars(select(shop.Tag.id)))
            with session.begin_nested():
                session.add_all(
                    shop.Tag(**row) for row in wanted if row["id"] not in known
                )
        session.commit()
        assert len(session.scalars(select(shop.Tag)).all()) == 20
