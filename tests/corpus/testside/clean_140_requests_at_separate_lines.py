"""Test code of clean_140: six requests, one per line, through one Session."""

from sqlalchemy.orm import Session
from tests.corpus.kit import Ctx
from tests.corpus.scenarios.clean_140_requests_at_separate_lines import order_page


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        assert order_page(session, 1)["customer"] == "customer1"
        assert order_page(session, 4)["customer"] == "customer2"
        assert order_page(session, 7)["customer"] == "customer3"
        assert order_page(session, 10)["customer"] == "customer4"
        assert order_page(session, 13)["customer"] == "customer5"
        assert order_page(session, 16)["customer"] == "customer6"
