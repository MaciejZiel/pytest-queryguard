"""Pagination and chunked IN loads are not reported as repeated statements.

The repeat rules recognise them from the statements and their bound parameters
(see "Pagination and chunks" in docs/how-detection-works.md). Every test that
expects a statement to be left alone has a twin that changes one property and
expects it to be reported.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from pytest_queryguard.collector import QueryCollector
from pytest_queryguard.detectors import (
    MIN_CHUNK,
    SCOPE_SESSION,
    RepeatFinding,
    detect,
)
from pytest_queryguard.events import CallSite, StatementEvent

SITE = CallSite(filename="app.py", lineno=10, function="handler")

OFFSET_SQL = 'SELECT "order".id FROM "order" ORDER BY "order".id LIMIT ? OFFSET ?'
# SQLAlchemy renders .limit() on SQLite as LIMIT ? OFFSET ? with an offset of 0.
KEYSET_SQL = (
    'SELECT "order".id FROM "order" WHERE "order".id > ? '
    'ORDER BY "order".id LIMIT ? OFFSET ?'
)


def _collector(
    runs: Sequence[tuple[str, object]], scopes: Sequence[int] | None = None
) -> QueryCollector:
    c = QueryCollector()
    for i, (sql, params) in enumerate(runs):
        scope = None if scopes is None else scopes[i]
        c.statements.append(
            StatementEvent(sql, SITE, None, parameters=params, scope=scope)
        )
    return c


def _repeats(c: QueryCollector, **kwargs: object) -> list[RepeatFinding]:
    found = detect(c, relationship_threshold=2, repeat_threshold=5, **kwargs)  # type: ignore[arg-type]
    return [f for f in found if isinstance(f, RepeatFinding)]


def _in_sql(n: int, extra: str = "") -> str:
    marks = ", ".join("?" for _ in range(n))
    return f"SELECT product.name FROM product WHERE product.id IN ({marks}){extra}"


def _chunks(ids: Sequence[object], size: int) -> list[tuple[str, object]]:
    out = []
    for start in range(0, len(ids), size):
        chunk = tuple(ids[start : start + size])
        out.append((_in_sql(len(chunk)), chunk))
    return out


# Pagination


def test_offset_pagination_is_not_reported() -> None:
    runs = [(OFFSET_SQL, (4, offset)) for offset in range(0, 28, 4)]
    assert _repeats(_collector(runs)) == []


def test_keyset_pagination_is_not_reported() -> None:
    runs = [(KEYSET_SQL, (last, 4, 0)) for last in range(0, 28, 4)]
    assert _repeats(_collector(runs)) == []


def test_descending_keyset_pagination_is_not_reported() -> None:
    sql = "SELECT id FROM t WHERE id < ? AND status = ? ORDER BY id DESC LIMIT ?"
    runs = [(sql, (last, "new", 5)) for last in (10**9, 24, 19, 14, 9, 4)]
    assert _repeats(_collector(runs)) == []


def test_keyset_on_a_date_column_is_reported() -> None:
    # How far a date cursor moved says nothing about the page: a loop that
    # passes each row's own date looks the same, so it is reported as before.
    sql = "SELECT id FROM t WHERE created >= ? ORDER BY created LIMIT ?"
    days = [f"2026-10-{d:02d}" for d in range(1, 8)]
    assert len(_repeats(_collector([(sql, (d, 50)) for d in days]))) == 1


def test_a_growing_keyset_bound_per_row_is_reported() -> None:
    # WHERE id > :row_id LIMIT 4 for each row in id order: the bound only grows,
    # but by one row, not by a page.
    runs = [(KEYSET_SQL, (last, 4, 0)) for last in range(1, 9)]
    assert len(_repeats(_collector(runs))) == 1


def test_a_short_last_keyset_step_ends_the_walk() -> None:
    # Newest first in pages of five over ids 24..1: the last page holds four
    # rows, so the final, empty query moves the cursor by four.
    sql = "SELECT id FROM t WHERE id < ? ORDER BY id DESC LIMIT ?"
    runs = [(sql, (last, 5)) for last in (10**9, 20, 15, 10, 5, 1)]
    assert _repeats(_collector(runs)) == []


def test_a_short_keyset_step_in_the_middle_is_reported() -> None:
    sql = "SELECT id FROM t WHERE id > ? ORDER BY id LIMIT ?"
    runs = [(sql, (last, 5)) for last in (0, 5, 7, 12, 17, 22)]
    assert len(_repeats(_collector(runs))) == 1


def test_a_growing_string_keyset_bound_is_reported() -> None:
    sql = "SELECT id FROM t WHERE name > ? ORDER BY name LIMIT ?"
    names = [f"customer{i}" for i in range(1, 9)]
    assert len(_repeats(_collector([(sql, (n, 2)) for n in names]))) == 1


def test_pyformat_parameters_are_read_by_name() -> None:
    sql = "SELECT id FROM t ORDER BY id LIMIT %(param_1)s OFFSET %(param_2)s"
    runs = [(sql, {"param_1": 4, "param_2": o}) for o in range(0, 28, 4)]
    assert _repeats(_collector(runs)) == []


def test_numbered_parameters_with_casts_are_read() -> None:
    sql = "SELECT id FROM t WHERE id > $1::INTEGER ORDER BY id LIMIT $2::INTEGER"
    runs = [(sql, (last, 4)) for last in range(0, 28, 4)]
    assert _repeats(_collector(runs)) == []


def test_literal_limit_and_offset_are_read_from_the_text() -> None:
    runs = [
        (f"SELECT id FROM t ORDER BY id LIMIT 4 OFFSET {o}", ())
        for o in range(0, 28, 4)
    ]
    assert _repeats(_collector(runs)) == []


def test_the_same_walk_twice_is_not_reported() -> None:
    walk = [(OFFSET_SQL, (8, offset)) for offset in (0, 8, 16, 24)]
    assert _repeats(_collector(walk + walk)) == []


def test_the_same_walk_in_a_loop_is_reported() -> None:
    # A loop-invariant paginated lookup called once per item.
    walk = [(KEYSET_SQL, (last, 2, 0)) for last in (0, 2, 4)]
    [finding] = _repeats(_collector(walk * 8))
    assert finding.count == 24


def test_a_second_walk_from_another_start_is_reported() -> None:
    runs = [(OFFSET_SQL, (8, o)) for o in (0, 8, 16, 24, 4, 12)]
    assert len(_repeats(_collector(runs))) == 1


def test_one_row_per_page_is_reported() -> None:
    # LIMIT 1 OFFSET n reads a table row by row (issue #60).
    runs = [(OFFSET_SQL, (1, offset)) for offset in range(10)]
    [finding] = _repeats(_collector(runs))
    assert finding.count == 10


def test_one_row_per_page_with_a_literal_limit_is_reported() -> None:
    runs = [(f"SELECT id FROM t ORDER BY id LIMIT 1 OFFSET {o}", ()) for o in range(10)]
    assert len(_repeats(_collector(runs))) == 1


def test_overlapping_pages_are_reported() -> None:
    # The offset moves by one row and the page holds five: not paging.
    runs = [(OFFSET_SQL, (5, offset)) for offset in range(8)]
    assert len(_repeats(_collector(runs))) == 1


def test_a_page_per_item_is_reported() -> None:
    # The first page of each customer's orders: the filter changes, not the cursor.
    sql = "SELECT id FROM orders WHERE customer_id = ? ORDER BY id LIMIT ? OFFSET ?"
    runs = [(sql, (customer, 3, 0)) for customer in range(1, 9)]
    assert len(_repeats(_collector(runs))) == 1


def test_a_keyset_bound_per_item_is_reported() -> None:
    # Bounds that go up and down come from the items of a loop.
    runs = [(KEYSET_SQL, (last, 4, 0)) for last in (5, 2, 9, 1, 7, 3)]
    assert len(_repeats(_collector(runs))) == 1


def test_the_same_page_again_is_reported() -> None:
    runs = [(OFFSET_SQL, (4, 0)) for _ in range(6)]
    assert len(_repeats(_collector(runs))) == 1


def test_a_range_without_limit_is_a_filter_and_is_reported() -> None:
    sql = "SELECT id FROM t WHERE created > ?"
    runs = [(sql, (day,)) for day in range(1, 8)]
    assert len(_repeats(_collector(runs))) == 1


def test_not_equal_is_not_a_keyset_bound() -> None:
    sql = "SELECT id FROM t WHERE status <> ? ORDER BY id LIMIT ?"
    runs = [(sql, (status, 5)) for status in ("a", "b", "c", "d", "e", "f")]
    assert len(_repeats(_collector(runs))) == 1


def test_unknown_parameters_are_reported() -> None:
    runs = [(OFFSET_SQL, None) for _ in range(6)]
    assert len(_repeats(_collector(runs))) == 1


def test_missing_positional_parameters_are_reported() -> None:
    runs = [(OFFSET_SQL, (4,)) for _ in range(6)]
    assert len(_repeats(_collector(runs))) == 1


def test_cursors_that_do_not_compare_are_reported() -> None:
    runs = [(KEYSET_SQL, (last, 4, 0)) for last in (1, "b", 3, "d", 5, "f")]
    assert len(_repeats(_collector(runs))) == 1


def test_a_walk_in_each_session_is_not_reported_per_session() -> None:
    walk = [(OFFSET_SQL, (4, offset)) for offset in range(0, 24, 4)]
    c = _collector(walk + walk, scopes=[1] * 6 + [2] * 6)
    assert _repeats(c, scope=SCOPE_SESSION) == []


def test_a_loop_next_to_a_walk_in_another_session_is_reported() -> None:
    walk = [(OFFSET_SQL, (4, offset)) for offset in range(0, 24, 4)]
    loop = [(OFFSET_SQL, (4, 0)) for _ in range(7)]
    c = _collector(walk + loop, scopes=[1] * 6 + [2] * 7)
    [finding] = _repeats(c, scope=SCOPE_SESSION)
    assert finding.count == 7


# Chunked IN


def test_chunked_in_is_not_reported() -> None:
    assert _repeats(_collector(_chunks(range(1, 3001), 500))) == []


def test_chunked_in_with_a_shorter_last_chunk_is_not_reported() -> None:
    assert _repeats(_collector(_chunks(range(1, 1101), 200))) == []


def test_the_shorter_chunk_may_come_first() -> None:
    runs = _chunks(range(1, 1101), 200)
    assert _repeats(_collector([runs[-1], *runs[:-1]])) == []


def test_chunked_in_of_string_keys_is_not_reported() -> None:
    names = [f"product{i}" for i in range(1, 1101)]
    assert _repeats(_collector(_chunks(names, 200))) == []


def test_chunked_in_with_named_parameters_is_not_reported() -> None:
    runs = []
    for start in range(0, 600, 100):
        names = [f"id_1_{i}" for i in range(1, 101)]
        sql = (
            "SELECT name FROM product WHERE id IN ("
            + ", ".join(f"%({n})s" for n in names)
            + ")"
        )
        runs.append((sql, {n: start + i for i, n in enumerate(names)}))
    assert _repeats(_collector(runs)) == []


def test_chunked_in_with_a_fixed_filter_is_not_reported() -> None:
    runs = [
        (_in_sql(len(chunk), " AND status = ?"), (*chunk, "new"))
        for chunk in (tuple(range(s, s + 50)) for s in range(0, 300, 50))
    ]
    assert _repeats(_collector(runs)) == []


def test_short_in_lists_are_reported() -> None:
    size = MIN_CHUNK - 1
    runs = _chunks(range(size * 6), size)
    assert len(_repeats(_collector(runs))) == 1


def test_ten_ids_per_item_are_reported() -> None:
    # A helper called with each customer's own ten orders: equal-sized lists
    # that share no value, from a loop over customers, not from one list.
    assert len(_repeats(_collector(_chunks(range(1, 81), 10)))) == 1


def test_one_id_per_in_list_is_reported() -> None:
    assert len(_repeats(_collector(_chunks(range(1, 11), 1)))) == 1


def test_overlapping_in_lists_are_reported() -> None:
    runs = [(_in_sql(20), tuple(range(s, s + 20))) for s in range(0, 60, 10)]
    assert len(_repeats(_collector(runs))) == 1


def test_in_lists_of_several_sizes_are_reported() -> None:
    sizes = [20, 15, 20, 12, 20, 20]
    runs, start = [], 0
    for size in sizes:
        runs.append((_in_sql(size), tuple(range(start, start + size))))
        start += size
    assert len(_repeats(_collector(runs))) == 1


def test_in_lists_with_another_changing_value_are_reported() -> None:
    runs = [
        (_in_sql(20, " AND customer_id = ?"), (*range(s, s + 20), customer))
        for customer, s in enumerate(range(0, 120, 20))
    ]
    assert len(_repeats(_collector(runs))) == 1


def test_identical_in_lists_are_reported() -> None:
    runs = [(_in_sql(20), tuple(range(20))) for _ in range(6)]
    assert len(_repeats(_collector(runs))) == 1


def test_unhashable_values_in_lists_do_not_break_the_rule() -> None:
    runs = [
        (_in_sql(MIN_CHUNK), tuple([i] for i in range(s, s + MIN_CHUNK)))
        for s in range(0, 6 * MIN_CHUNK, MIN_CHUNK)
    ]
    assert _repeats(_collector(runs)) == []


# The small-repeat rule


def test_small_repeat_leaves_three_chunks_alone() -> None:
    c = _collector(_chunks(range(1, 1501), 500))
    assert _repeats(c, small_repeat_threshold=2) == []


def test_small_repeat_still_reports_three_lookups() -> None:
    runs = [("SELECT name FROM product WHERE id = ?", (i,)) for i in (1, 2, 3)]
    [finding] = _repeats(_collector(runs), small_repeat_threshold=2)
    assert finding.varying


@pytest.mark.parametrize("pages", [5, 50])
def test_offset_pagination_of_any_length_is_not_reported(pages: int) -> None:
    runs = [(OFFSET_SQL, (10, 10 * page)) for page in range(pages)]
    assert _repeats(_collector(runs)) == []
