"""Test code of clean_142: six requests from a loop of the test, one Session."""

from sqlalchemy.orm import Session
from tests.corpus.kit import Ctx
from tests.corpus.scenarios.clean_142_requests_in_a_test_loop import order_page


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        for number, order_id in enumerate((1, 4, 7, 10, 13, 16), start=1):
            assert order_page(session, order_id)["customer"] == f"customer{number}"
