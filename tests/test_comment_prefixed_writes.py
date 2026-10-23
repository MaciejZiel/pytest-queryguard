from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from pytest_queryguard import listeners, record_queries, sqltext

# pytester-free, but the outer plugin watches these queries too.
pytestmark = pytest.mark.allow_nplusone

PREFIXES = ["", "/* traceparent=00-abc-def-01 */ ", "-- tag\n", "  /* a */ -- b\n"]


def _run(prefix: str, **kwargs: int) -> list[str]:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE t (a INTEGER)"))
        with record_queries() as log:
            for i in range(6):
                conn.execute(text(f"{prefix}INSERT INTO t (a) VALUES (:a)"), {"a": i})
    try:
        log.assert_no_nplusone(**kwargs)
    except AssertionError as exc:
        return [str(exc)]
    return []


@pytest.mark.parametrize("prefix", PREFIXES)
def test_an_insert_loop_is_not_a_repeated_statement(prefix: str) -> None:
    assert _run(prefix, repeat_threshold=5) == []


@pytest.mark.parametrize("prefix", PREFIXES)
def test_an_insert_loop_is_a_write_loop(prefix: str) -> None:
    (message,) = _run(prefix, write_loop_threshold=5)
    assert "the same INSERT ran 6 times" in message
    assert "WHERE id IN" not in message


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("verb", ["INSERT", "update", "Delete"])
def test_is_write_skips_leading_comments(prefix: str, verb: str) -> None:
    assert sqltext.is_write(f"{prefix}{verb} x")
    assert listeners._is_write(f"{prefix}{verb} x")


@pytest.mark.parametrize(
    "sql", ["", "SELECT 1", "/* INSERT */ SELECT 1", "-- INSERT\nSELECT 1", "/* open"]
)
def test_is_write_rejects_other_statements(sql: str) -> None:
    assert not sqltext.is_write(sql)
