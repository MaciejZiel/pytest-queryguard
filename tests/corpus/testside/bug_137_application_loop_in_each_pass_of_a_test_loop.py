"""Test code of bug_137: a loop of the test, each pass an application loop."""

from sqlalchemy.orm import Session
from tests.corpus.kit import Ctx
from tests.corpus.scenarios.bug_137_application_loop_in_each_pass_of_a_test_loop import (
    order_names,
)


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        for first in (1, 7, 13):
            assert len(order_names(session, [first, first + 3])) == 2
            session.expunge_all()
