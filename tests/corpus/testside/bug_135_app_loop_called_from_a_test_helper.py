"""Test code of bug_135: a helper that calls the application."""

from sqlalchemy.orm import Session
from tests.corpus.kit import Ctx
from tests.corpus.scenarios.bug_135_app_loop_called_from_a_test_helper import (
    order_rows,
)


def get(session: Session) -> list[dict[str, object]]:
    return order_rows(session)


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        assert len(get(session)) == 24
