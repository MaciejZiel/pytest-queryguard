from __future__ import annotations

import pytest

from pytest_queryguard.collector import QueryCollector
from pytest_queryguard.detectors import (
    RelationshipFinding,
    RepeatFinding,
    detect,
    normalize,
)
from pytest_queryguard.events import CallSite, RelationshipLoadEvent, StatementEvent

SITE = CallSite(filename="app.py", lineno=10, function="handler")
OTHER = CallSite(filename="app.py", lineno=20, function="other")


def _loads(
    rel: str, keys: list[object], site: CallSite | None = SITE
) -> QueryCollector:
    c = QueryCollector()
    c.relationship_loads.extend(RelationshipLoadEvent(rel, k, site) for k in keys)
    return c


def _stmts(sql: str, n: int, relationship: str | None = None) -> list[StatementEvent]:
    return [StatementEvent(sql, SITE, relationship) for _ in range(n)]


def test_normalize_collapses_in_lists() -> None:
    a = normalize("SELECT * FROM t WHERE id IN (?, ?, ?)")
    b = normalize("SELECT * FROM t WHERE id IN (?)")
    assert a == b


def test_normalize_replaces_literals() -> None:
    a = normalize("SELECT *  FROM t\n WHERE name = 'bob' AND age > 42 ")
    b = normalize("SELECT * FROM t WHERE name = 'o''neil' AND age > 7.5")
    assert a == b == "SELECT * FROM t WHERE name = ? AND age > ?"


def test_normalize_keeps_digits_in_identifiers() -> None:
    assert normalize("SELECT t1.id FROM t1 WHERE t1.x = 3") == (
        "SELECT t1.id FROM t1 WHERE t1.x = ?"
    )


def test_relationship_below_threshold_not_reported() -> None:
    c = _loads("Order.customer", [1])
    assert detect(c, relationship_threshold=2, repeat_threshold=5) == []


def test_relationship_reported_at_threshold() -> None:
    c = _loads("Order.customer", [1, 2])
    c.statements.append(StatementEvent("SELECT 1 FROM c", SITE, "Order.customer"))
    [f] = detect(c, relationship_threshold=2, repeat_threshold=5)
    assert isinstance(f, RelationshipFinding)
    assert (f.relationship, f.count, f.first) == ("Order.customer", 2, SITE)
    assert f.sample_sql == "SELECT 1 FROM c"


def test_relationship_sample_sql_none_without_statement() -> None:
    [f] = detect(_loads("A.b", [1, 2]), relationship_threshold=2, repeat_threshold=5)
    assert isinstance(f, RelationshipFinding)
    assert f.sample_sql is None


def test_same_parent_twice_counts_once_for_threshold() -> None:
    c = _loads("Order.customer", [1, 1, 1])
    assert detect(c, relationship_threshold=2, repeat_threshold=5) == []


def test_relationship_count_is_number_of_loads() -> None:
    c = _loads("Order.customer", [1, 1, 2])
    [f] = detect(c, relationship_threshold=2, repeat_threshold=5)
    assert isinstance(f, RelationshipFinding)
    assert f.count == 3


def test_none_parent_keys_are_ignored() -> None:
    c = _loads("Order.items", [None, None, None])
    assert detect(c, relationship_threshold=2, repeat_threshold=5) == []
    c = _loads("Order.items", [None, 1, None])
    assert detect(c, relationship_threshold=2, repeat_threshold=5) == []


def test_repeat_reported_at_five_not_four() -> None:
    sql = "SELECT * FROM t WHERE id = 1"
    c = QueryCollector()
    c.statements.extend(_stmts(sql, 4))
    assert detect(c, relationship_threshold=2, repeat_threshold=5) == []
    c.statements.append(StatementEvent("SELECT * FROM t WHERE id = 2", OTHER, None))
    [f] = detect(c, relationship_threshold=2, repeat_threshold=5)
    assert isinstance(f, RepeatFinding)
    assert f.count == 5
    assert f.sql == "SELECT * FROM t WHERE id = ?"
    assert f.first == SITE


def test_writes_never_reported_as_repeat() -> None:
    c = QueryCollector()
    c.statements.extend(_stmts("INSERT INTO t (a) VALUES (1)", 6))
    c.statements.extend(_stmts("update t set a = 1", 6))
    c.statements.extend(_stmts("  DELETE FROM t WHERE id = 1", 6))
    assert detect(c, relationship_threshold=2, repeat_threshold=5) == []


def test_relationship_tagged_sql_not_double_reported() -> None:
    c = _loads("Order.customer", [1, 2, 3, 4, 5, 6])
    c.statements.extend(_stmts("SELECT * FROM c WHERE id = 1", 6, "Order.customer"))
    findings = detect(c, relationship_threshold=2, repeat_threshold=5)
    assert [type(f) for f in findings] == [RelationshipFinding]


def test_ordering_relationships_first_then_repeats_by_count_desc() -> None:
    c = _loads("A.x", [1, 2])
    c.relationship_loads.extend(RelationshipLoadEvent("B.y", k, None) for k in range(5))
    c.statements.extend(_stmts("SELECT 1 FROM p", 5))
    c.statements.extend(_stmts("SELECT 1 FROM q", 8))
    f = detect(c, relationship_threshold=2, repeat_threshold=5)
    assert [type(x) for x in f] == [RelationshipFinding] * 2 + [RepeatFinding] * 2
    assert [x.count for x in f] == [5, 2, 8, 5]


@pytest.mark.parametrize(
    ("many", "one"),
    [
        ("IN (%s, %s, %s)", "IN (%s)"),
        ("IN ($1, $2, $3)", "IN ($1)"),
        ("IN (:id_1, :id_2)", "IN (:id_1)"),
        ("IN (%(id_1)s, %(id_2)s)", "IN (%(id_1)s)"),
        ("IN ('a', 'b')", "IN ('a')"),
        ("IN (1, 2, 3)", "IN (4)"),
        ("IN ($1::INTEGER, $2::INTEGER)", "IN ($1::INTEGER)"),
        ("IN (%(id_1)s::INTEGER, %(id_2)s::INTEGER)", "IN (%(id_1)s::INTEGER)"),
        ("IN (?::int[], ?::int[])", "IN (?::int[])"),
        ("IN ($1::VARCHAR(20), $2::VARCHAR(20))", "IN ($1::VARCHAR(20))"),
        ("IN ($1::INTEGER, $2::INTEGER)", "IN (?)"),
        ("IN ($1::DOUBLE PRECISION, $2::DOUBLE PRECISION)", "IN (?)"),
        ("IN (%s::CHARACTER VARYING(20), %s::CHARACTER VARYING(20))", "IN (?)"),
        (
            "IN ($1::TIMESTAMP WITHOUT TIME ZONE, $2::TIMESTAMP WITHOUT TIME ZONE)",
            "IN (?)",
        ),
        (
            "IN ($1::TIMESTAMP(3) WITH TIME ZONE, $2::TIMESTAMP(3) WITH TIME ZONE)",
            "IN (?)",
        ),
        ("IN ($1::NUMERIC(10, 2)[], $2::NUMERIC(10, 2)[])", "IN (?)"),
        ("IN ($1::public.mood, $2::public.mood)", "IN (?)"),
        ('IN ($1::"Sales"."Mood", $2::"Sales"."Mood")', "IN (?)"),
        ("IN (CAST(? AS INTEGER), CAST(? AS INTEGER))", "IN (?)"),
        (
            "IN (cast(:id_1 as double precision), cast(:id_2 as double precision))",
            "IN (?)",
        ),
        ("IN (CAST(%s AS public.mood), CAST(%s AS public.mood))", "IN (?)"),
        ("IN (CAST($1 AS VARCHAR(20)), $2::VARCHAR(20))", "IN (?)"),
    ],
)
def test_normalize_collapses_in_lists_for_all_placeholder_styles(
    many: str, one: str
) -> None:
    assert normalize(f"SELECT * FROM t WHERE id {many}") == normalize(
        f"SELECT * FROM t WHERE id {one}"
    )


@pytest.mark.parametrize(
    "sql",
    [
        # a column, not a placeholder, inside CAST(): not a parameter list
        "SELECT * FROM t WHERE id IN (CAST(t.a AS INTEGER), ?)",
        # a subquery is not a parameter list
        "SELECT * FROM t WHERE id IN (SELECT ?::INTEGER FROM u)",
    ],
)
def test_normalize_leaves_non_parameter_lists_alone(sql: str) -> None:
    assert "IN (...)" not in normalize(sql)


def test_normalize_keeps_casts_outside_in_lists() -> None:
    sql = "SELECT * FROM t WHERE x = CAST(? AS DOUBLE PRECISION)"
    assert normalize(sql) == sql


def test_normalize_numbered_params_are_stable() -> None:
    a = normalize("SELECT * FROM t WHERE id = $1")
    assert (
        a
        == normalize("SELECT * FROM t WHERE id = $2")
        == ("SELECT * FROM t WHERE id = ?")
    )


def test_first_callsite_skips_none() -> None:
    c = QueryCollector()
    c.relationship_loads.append(RelationshipLoadEvent("A.b", 1, None))
    c.relationship_loads.append(RelationshipLoadEvent("A.b", 2, SITE))
    c.statements.append(StatementEvent("SELECT 1", None, None))
    c.statements.extend(_stmts("SELECT 1", 4))
    rel, rep = detect(c, relationship_threshold=2, repeat_threshold=5)
    assert rel.first == SITE and rep.first == SITE
