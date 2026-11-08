"""Test code of bug_136: requests on separate lines, one of them an application loop."""

from sqlalchemy.orm import Session
from tests.corpus.kit import Ctx
from tests.corpus.scenarios.bug_136_lookup_loop_among_separate_requests import (
    customer_page,
    product_names,
)


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        assert customer_page(session, 1) == "customer1"
        assert len(product_names(session, [1, 2, 3, 4, 5, 6])) == 6
        assert customer_page(session, 2) == "customer2"
