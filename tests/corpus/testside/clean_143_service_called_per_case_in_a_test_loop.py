"""Test code of clean_143: the arrange step places orders one by one in a loop."""

import pytest
from sqlalchemy.orm import Session
from tests.corpus.kit import Ctx
from tests.corpus.scenarios.clean_143_service_called_per_case_in_a_test_loop import (
    LIMIT,
    LimitReached,
    place_order,
)


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        # customer 1 has three orders from the seed: five more reach the limit
        for order_id in range(1001, 1001 + LIMIT - 3):
            place_order(session, 1, order_id)
        with pytest.raises(LimitReached):
            place_order(session, 1, 2000)
