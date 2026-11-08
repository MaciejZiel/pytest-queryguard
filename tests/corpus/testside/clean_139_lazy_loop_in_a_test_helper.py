"""Test code of clean_139: a helper that builds the expected values lazily."""

from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.corpus.domains import shop
from tests.corpus.kit import Ctx
from tests.corpus.scenarios.clean_139_lazy_loop_in_a_test_helper import order_counts


def expected_counts(session: Session) -> dict[str, int]:
    return {c.name: len(c.orders) for c in session.scalars(select(shop.Customer))}


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        expected = expected_counts(session)
    with Session(ctx.engine()) as session:
        assert order_counts(session) == expected
