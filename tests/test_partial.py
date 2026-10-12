"""Which partial indexes the generic plan cannot rule out (no server needed)."""

from __future__ import annotations

import pytest

from pytest_queryguard.partial import PartialIndex, may_serve, predicate_columns

COLUMNS = frozenset({"status", "x", "flag", "deleted_at", "Name"})


def index(predicate: str) -> PartialIndex:
    return PartialIndex("public", "t1", "t1_partial", predicate, COLUMNS)


@pytest.mark.parametrize(
    ("predicate", "expected"),
    [
        ("((status)::text = 'open'::text)", {"status"}),
        ("(deleted_at IS NULL)", {"deleted_at"}),
        ("(flag AND (status)::text <> 'x status'::text)", {"flag", "status"}),
        ('("Name" IS NOT NULL)', {"Name"}),
        ("(x > 5)", {"x"}),
        ("(other > 5)", set()),
    ],
)
def test_predicate_columns(predicate: str, expected: set[str]) -> None:
    assert predicate_columns(predicate, COLUMNS) == expected


FILTER = "(((t1.status)::text = $1) AND (t1.x = $2))"


@pytest.mark.parametrize(
    ("predicate", "conditions", "serves"),
    [
        # the repro: the generic plan cannot see the bound value of status
        ("((status)::text = 'open'::text)", [FILTER], True),
        # the predicate column is not filtered at all
        ("(flag)", [FILTER], False),
        # the statement fixes the predicate column with a literal: decided
        ("(deleted_at IS NULL)", ["((t1.deleted_at IS NULL) AND (t1.x = $1))"], False),
        # one predicate column is missing from the statement
        ("(flag AND ((status)::text = 'open'::text))", [FILTER], False),
        # every predicate column is filtered, one with a parameter
        (
            "(flag AND ((status)::text = 'open'::text))",
            ["((t1.flag IS TRUE) AND ((t1.status)::text = $1))"],
            True,
        ),
        # an unparsable condition keeps the finding
        ("((status)::text = 'open'::text)", ["(t1.status = $1) COLLATE ??"], False),
        # no conditions at all
        ("((status)::text = 'open'::text)", [], False),
    ],
)
def test_may_serve(predicate: str, conditions: list[str], serves: bool) -> None:
    assert bool(may_serve([index(predicate)], conditions, "t1")) is serves
