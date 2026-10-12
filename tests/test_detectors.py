from __future__ import annotations

import pytest

from pytest_queryguard.collector import QueryCollector
from pytest_queryguard.detectors import (
    ColumnLoadFinding,
    RelationshipFinding,
    RepeatFinding,
    _value_rows,
    detect,
    normalize,
)
from pytest_queryguard.events import (
    CallSite,
    ColumnLoadEvent,
    RelationshipLoadEvent,
    StatementEvent,
)

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


# ------------------------------------------------------------- column loads


def _column_loads(
    owner: str,
    keys: list[object],
    cause: str = "deferred",
    attributes: tuple[str, ...] = ("blob",),
) -> QueryCollector:
    from pytest_queryguard.events import ColumnLoadEvent

    c = QueryCollector()
    c.column_loads.extend(
        ColumnLoadEvent(owner, attributes, cause, k, SITE, f"SELECT {owner}")
        for k in keys
    )
    return c


def test_column_loads_below_the_threshold_are_not_reported() -> None:
    c = _column_loads("Child", list(range(4)))
    assert detect(c, relationship_threshold=2, repeat_threshold=5) == []
    found = detect(
        c, relationship_threshold=2, repeat_threshold=5, column_load_threshold=4
    )
    assert [f.label for f in found] == ["Child.blob"]  # type: ignore[union-attr]


def test_column_load_threshold_counts_distinct_instances() -> None:
    c = _column_loads("Child", [1] * 10)
    assert detect(c, relationship_threshold=2, repeat_threshold=5) == []


def test_column_loads_are_grouped_by_class_attributes_and_cause() -> None:
    c = _column_loads("Child", list(range(5)))
    c.column_loads.extend(
        _column_loads("Child", list(range(5)), "expired").column_loads
    )
    c.column_loads.extend(
        _column_loads("Child", list(range(5)), attributes=("note",)).column_loads
    )
    c.column_loads.extend(_column_loads("Parent", [1]).column_loads)
    labels = sorted(
        f.label  # type: ignore[union-attr]
        for f in detect(c, relationship_threshold=2, repeat_threshold=5)
    )
    assert labels == ["Child (expired)", "Child.blob", "Child.note"]


def test_expired_loads_ignore_the_attributes_when_grouping() -> None:
    c = _column_loads("Parent", list(range(3)), "expired", ("name",))
    c.column_loads.extend(
        _column_loads(
            "Parent", list(range(3, 6)), "expired", ("id", "name")
        ).column_loads
    )
    (finding,) = detect(c, relationship_threshold=2, repeat_threshold=5)
    assert finding.count == 6  # type: ignore[union-attr]
    assert finding.instances == 6  # type: ignore[union-attr]


def test_column_load_statements_are_not_a_repeated_statement() -> None:
    c = QueryCollector()
    c.statements.extend(
        StatementEvent("SELECT blob FROM t WHERE id = 1", SITE, None, column_load=True)
        for _ in range(8)
    )
    assert detect(c, relationship_threshold=2, repeat_threshold=5) == []
    c.statements.extend(_stmts("SELECT blob FROM t WHERE id = 1", 5))
    assert len(detect(c, relationship_threshold=2, repeat_threshold=5)) == 1


def test_relationship_finding_remembers_a_scalar_relationship() -> None:
    c = QueryCollector()
    c.relationship_loads.extend(
        RelationshipLoadEvent("Order.customer", k, SITE, True) for k in (1, 2)
    )
    (finding,) = detect(c, relationship_threshold=2, repeat_threshold=5)
    assert isinstance(finding, RelationshipFinding)
    assert finding.scalar


def test_normalize_is_stable_across_cached_calls() -> None:
    from pytest_queryguard.detectors import normalize

    sql = "SELECT * FROM t WHERE id IN (1, 2, 3) AND name = 'x' AND n = 42"
    expected = "SELECT * FROM t WHERE id IN (...) AND name = ? AND n = ?"
    assert normalize(sql) == expected
    assert normalize(sql) == expected  # second call is served from the cache
    assert normalize(sql.replace("42", "43")) == expected


# ------------------------------------------- relationship rule: edge cases


def _rel_load(
    rel: str, key: object, scope: int | None, scalar: bool = False
) -> RelationshipLoadEvent:
    return RelationshipLoadEvent(rel, key, SITE, scalar, scope)


def test_relationship_threshold_of_one_reports_a_single_parent() -> None:
    c = _loads("Order.customer", [1])
    [f] = detect(c, relationship_threshold=1, repeat_threshold=5)
    assert isinstance(f, RelationshipFinding)
    assert (f.count, f.scopes, f.scoped) == (1, 1, False)


def test_relationship_below_threshold_does_not_hide_a_later_relationship() -> None:
    c = QueryCollector()
    c.relationship_loads.append(_rel_load("A.small", 1, None))
    c.relationship_loads.extend(_rel_load("B.big", k, None) for k in (1, 2, 3))
    [f] = detect(c, relationship_threshold=3, repeat_threshold=5)
    assert isinstance(f, RelationshipFinding)
    assert f.relationship == "B.big"


def test_relationship_per_session_reports_the_session_with_most_parents() -> None:
    c = QueryCollector()
    c.relationship_loads.extend(_rel_load("A.b", k, 1) for k in (1, 2))
    c.relationship_loads.extend(_rel_load("A.b", k, 2) for k in (1, 2, 3))
    c.statements.append(StatementEvent("SELECT one", SITE, "A.b", scope=1))
    c.statements.append(StatementEvent("SELECT two", SITE, "A.b", scope=2))
    [f] = detect(c, relationship_threshold=2, repeat_threshold=5, scope="session")
    assert isinstance(f, RelationshipFinding)
    assert (f.count, f.scopes, f.scoped) == (3, 1, True)
    assert f.sample_sql == "SELECT two"


def test_relationship_per_session_tie_keeps_the_first_session() -> None:
    c = QueryCollector()
    # Same number of parents in both Sessions; the first Session loaded more.
    c.relationship_loads.extend(_rel_load("A.b", k, 1) for k in (1, 1, 1, 2))
    c.relationship_loads.extend(_rel_load("A.b", k, 2) for k in (1, 2))
    [f] = detect(c, relationship_threshold=2, repeat_threshold=5, scope="session")
    assert isinstance(f, RelationshipFinding)
    assert f.count == 4


def test_relationship_sample_sql_skips_other_relationships_statements() -> None:
    c = _loads("A.b", [1, 2])
    c.statements.append(StatementEvent("SELECT other", SITE, "C.d"))
    c.statements.append(StatementEvent("SELECT mine", SITE, "A.b"))
    [f] = detect(c, relationship_threshold=2, repeat_threshold=5)
    assert isinstance(f, RelationshipFinding)
    assert f.sample_sql == "SELECT mine"


def test_relationship_sample_sql_is_the_first_statement_across_sessions() -> None:
    c = QueryCollector()
    c.relationship_loads.extend(_rel_load("A.b", k, 1) for k in (1, 2))
    c.statements.append(StatementEvent("SELECT first", SITE, "A.b", scope=2))
    c.statements.append(StatementEvent("SELECT second", SITE, "A.b", scope=1))
    [f] = detect(c, relationship_threshold=2, repeat_threshold=5)
    assert isinstance(f, RelationshipFinding)
    assert f.sample_sql == "SELECT first"
    assert f.scopes == 1


def test_relationship_counts_the_sessions_it_came_from() -> None:
    c = QueryCollector()
    c.relationship_loads.extend(_rel_load("A.b", k, k % 3) for k in range(6))
    [f] = detect(c, relationship_threshold=2, repeat_threshold=5)
    assert isinstance(f, RelationshipFinding)
    assert (f.count, f.scopes, f.scoped) == (6, 3, False)


# ------------------------------------------- column-load rule: edge cases


def _column_events(
    owner: str,
    keys: list[object],
    *,
    scope: int | None = None,
    site: CallSite | None = SITE,
    cause: str = "deferred",
    sql: str | None = "SELECT 1",
) -> list[ColumnLoadEvent]:
    return [ColumnLoadEvent(owner, ("blob",), cause, k, site, sql, scope) for k in keys]


def test_column_load_threshold_of_one_reports_a_single_instance() -> None:
    c = QueryCollector()
    c.column_loads.extend(_column_events("Child", [1]))
    [f] = detect(
        c, relationship_threshold=2, repeat_threshold=5, column_load_threshold=1
    )
    assert isinstance(f, ColumnLoadFinding)
    assert (f.count, f.instances, f.scopes, f.scoped) == (1, 1, 1, False)


def test_column_load_below_threshold_does_not_hide_a_later_class() -> None:
    c = QueryCollector()
    c.column_loads.extend(_column_events("Small", [1]))
    c.column_loads.extend(_column_events("Big", [1, 2, 3]))
    [f] = detect(
        c, relationship_threshold=2, repeat_threshold=5, column_load_threshold=3
    )
    assert isinstance(f, ColumnLoadFinding)
    assert f.owner == "Big"


def test_non_deferred_column_loads_have_no_attributes() -> None:
    c = QueryCollector()
    c.column_loads.extend(_column_events("Child", [1, 2], cause="expired"))
    [f] = detect(
        c, relationship_threshold=2, repeat_threshold=5, column_load_threshold=2
    )
    assert isinstance(f, ColumnLoadFinding)
    assert f.attributes == ()


def test_column_loads_without_callsite_or_sql_are_still_reported() -> None:
    c = QueryCollector()
    c.column_loads.extend(_column_events("Child", [1, 2], site=None, sql=None))
    [f] = detect(
        c, relationship_threshold=2, repeat_threshold=5, column_load_threshold=2
    )
    assert isinstance(f, ColumnLoadFinding)
    assert (f.first, f.sample_sql) == (None, None)


def test_column_loads_per_session_report_the_busiest_session() -> None:
    c = QueryCollector()
    c.column_loads.extend(_column_events("Child", [1, 2], scope=1))
    c.column_loads.extend(_column_events("Child", [1, 2, 3], scope=2))
    [f] = detect(
        c,
        relationship_threshold=2,
        repeat_threshold=5,
        column_load_threshold=2,
        scope="session",
    )
    assert isinstance(f, ColumnLoadFinding)
    assert (f.count, f.instances, f.scopes, f.scoped) == (3, 3, 1, True)


def test_column_loads_per_session_tie_keeps_the_first_session() -> None:
    c = QueryCollector()
    c.column_loads.extend(_column_events("Child", [1, 1, 1, 2], scope=1))
    c.column_loads.extend(_column_events("Child", [1, 2], scope=2))
    [f] = detect(
        c,
        relationship_threshold=2,
        repeat_threshold=5,
        column_load_threshold=2,
        scope="session",
    )
    assert isinstance(f, ColumnLoadFinding)
    assert f.count == 4


def test_column_load_findings_are_sorted_by_count_descending() -> None:
    c = QueryCollector()
    c.column_loads.extend(_column_events("Few", [1, 2]))
    c.column_loads.extend(_column_events("Many", [1, 2, 3, 4]))
    found = detect(
        c, relationship_threshold=2, repeat_threshold=5, column_load_threshold=2
    )
    assert [f.owner for f in found if isinstance(f, ColumnLoadFinding)] == [
        "Many",
        "Few",
    ]


def test_column_loads_count_the_sessions_they_came_from() -> None:
    c = QueryCollector()
    c.column_loads.extend(_column_events("Child", [1, 2], scope=1))
    c.column_loads.extend(_column_events("Child", [3, 4], scope=2))
    [f] = detect(
        c, relationship_threshold=2, repeat_threshold=5, column_load_threshold=2
    )
    assert isinstance(f, ColumnLoadFinding)
    assert (f.count, f.scopes, f.scoped) == (4, 2, False)


# ------------------------------------------------- counting VALUES rows


@pytest.mark.parametrize(
    ("sql", "rows"),
    [
        ("INSERT INTO t (a) VALUES (1)", 1),
        ("INSERT INTO t (a) VALUES (1), (2), (3)", 3),
        ("insert into t (a) values (1), (2)", 2),
        ("UPDATE t SET a = 1 WHERE id = 2", 1),
        ("INSERT INTO t (a) VALUES (1), (2) RETURNING id", 2),
        ("INSERT INTO t (a) VALUES (1) ON CONFLICT DO NOTHING", 1),
        ("INSERT INTO t (a, b) VALUES (?, ?),\n\t(?, ?)", 2),
        # string literals: quotes, escaped quotes and parentheses are not rows
        ("INSERT INTO t (a) VALUES ('x'), ('y')", 2),
        ("INSERT INTO t (a) VALUES (''), ('')", 2),
        ("INSERT INTO t (a, b) VALUES ('it''s', 1), ('x', 2)", 2),
        ("INSERT INTO t (a, b) VALUES ('a''', 1), ('b', 2)", 2),
        ("INSERT INTO t (a, b) VALUES ('''', 1), ('b', 2)", 2),
        ("INSERT INTO t (a, b) VALUES ('(', 1), (')', 2)", 2),
        ("INSERT INTO t (a, b) VALUES ('ab', 1), ('cde', 2)", 2),
        # an unterminated literal ends the scan instead of raising
        ("INSERT INTO t (a) VALUES ('abc", 1),
        ("INSERT INTO t (a) VALUES (1), ('abc", 2),
    ],
)
def test_value_rows_counts_the_row_tuples(sql: str, rows: int) -> None:
    assert _value_rows(sql) == rows
