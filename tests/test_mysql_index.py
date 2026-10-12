"""The plan analysis for MySQL and MariaDB on recorded EXPLAIN output.

tests/fixtures/plans/mysql8 and mariadb11 were captured by
scripts/capture_mysql_plans.py from MySQL 8.4.11 and MariaDB 11.8.9. These
tests need no database: the catalog answers come from the fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pytest_queryguard import mysql_index
from pytest_queryguard.events import ScanIssue

PLANS = Path(__file__).parent / "fixtures" / "plans"
SERVERS = ["mysql8", "mariadb11"]


class FixtureSchema(mysql_index.Schema):
    """The catalog facts recorded next to a plan."""

    def __init__(self, catalog: dict[str, Any]) -> None:  # no cursor
        self.catalog = catalog

    def database(self) -> str | None:
        return "qg_capture"

    def table(self, name: str) -> str | None:
        for key, facts in self.catalog.items():
            if key.lower() == name.lower():
                return facts["table"]  # type: ignore[no-any-return]
        return None

    def columns(self, table: str) -> list[str]:
        return list(self.catalog[table]["columns"])

    def leading(self, table: str) -> frozenset[str]:
        return frozenset(self.catalog[table]["leading"])


def analyse(server: str, name: str) -> list[ScanIssue]:
    document = json.loads((PLANS / server / f"{name}.json").read_text())
    return mysql_index.analyse(
        document["explain"], document["sql"], FixtureSchema(document["catalog"])
    )


def ix(table: str, *columns: str) -> str:
    name = f"ix_{table}_{'_'.join(columns)}"
    return f"CREATE INDEX {name} ON {table} ({', '.join(columns)})"


def summary(issues: list[ScanIssue]) -> list[tuple[str, str, str, str | None]]:
    return [
        (
            i.table,
            i.kind,
            i.node.split(" (")[0],
            None if i.advice is None else i.advice.create_sql,
        )
        for i in issues
    ]


# name -> [(table, kind, node start, CREATE INDEX or None)], the same on both servers.
SCAN = "full table scan"
INDEX = "full index scan"
EXPECTED: dict[str, list[tuple[str, str, str, str | None]]] = {
    # a filter without an index
    "eq_unindexed": [("orders", "filter", SCAN, ix("orders", "customer_id"))],
    "in_list": [("orders", "filter", SCAN, ix("orders", "customer_id"))],
    "range_unindexed": [("orders", "filter", SCAN, ix("orders", "total"))],
    "is_null": [("orders", "filter", SCAN, ix("orders", "customer_id"))],
    "composite_leading_missing": [("orders", "filter", SCAN, ix("orders", "created"))],
    "order_limit_filter": [("orders", "filter", SCAN, ix("orders", "customer_id"))],
    "group_by": [("orders", "filter", SCAN, ix("orders", "total"))],
    "delete_where": [("orders", "filter", SCAN, ix("orders", "total"))],
    "update_where": [("orders", "filter", INDEX, ix("orders", "customer_id"))],
    "tiny_unindexed": [("tiny_plain", "filter", SCAN, ix("tiny_plain", "label"))],
    # joins
    "join_unindexed": [("orders", "join condition", SCAN, ix("orders", "customer_id"))],
    "straight_join": [("orders", "join condition", SCAN, ix("orders", "customer_id"))],
    "self_join": [("orders", "join condition", SCAN, ix("orders", "customer_id"))],
    # subqueries, derived tables and CTEs are looked through
    "subquery_in": [("orders", "filter", SCAN, ix("orders", "total"))],
    "derived_table": [("orders", "filter", SCAN, ix("orders", "total"))],
    "cte": [("orders", "filter", SCAN, ix("orders", "total"))],
    # index hints are not table aliases
    "index_hint": [("orders", "filter", SCAN, ix("orders", "customer_id"))],
    # no usable columns: the generic hint
    "or_two_columns": [("orders", "filter", SCAN, None)],
    "union_same_table": [("orders", "filter", SCAN, None)],
    "function_of_column": [("customers", "filter", SCAN, None)],
}
# False-positive traps: the plan scans or reads an index, and nothing is missing.
NOTHING = [
    "eq_indexed",  # ref
    "pk_lookup",  # const
    "join_indexed",  # ref on the composite index
    "join_no_filter",  # the join starts at orders; someone has to be scanned
    "covering_count",  # index scan, no filter
    "composite_leading_present",
    "like_leading_wildcard",  # a btree cannot serve it
    "order_limit_no_filter",  # Using filesort is not a scan to blame
    "select_all",
    "fulltext",
    "status_low_selectivity",
    "indexed_but_scanned",  # ALL although ix_orders_status_created could serve
    "tiny_indexed",
    "two_columns_same_table",
    "not_equal",
    "type_mismatch",  # the index exists; the cast is the problem
    "update_pk",
    "view",  # the SQL names the view, not the table behind it
    "schema_qualified_other_db",  # the catalog is the current database's
]
# MySQL 8 plans this with an index skip scan (type range); MariaDB reads the
# whole index.
DIFFERENT: dict[str, dict[str, list[tuple[str, str, str, str | None]]]] = {
    "covering_scan_filter": {
        "mysql8": [],
        "mariadb11": [("orders", "filter", INDEX, ix("orders", "created"))],
    }
}


@pytest.mark.parametrize("server", SERVERS)
@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_a_scan_without_an_index_is_reported(server: str, name: str) -> None:
    assert summary(analyse(server, name)) == EXPECTED[name]


@pytest.mark.parametrize("server", SERVERS)
@pytest.mark.parametrize("name", NOTHING)
def test_no_finding_where_nothing_is_missing(server: str, name: str) -> None:
    assert analyse(server, name) == []


@pytest.mark.parametrize("server", SERVERS)
def test_plans_that_differ_between_the_servers(server: str) -> None:
    for name, expected in DIFFERENT.items():
        assert summary(analyse(server, name)) == expected[server]


def test_every_fixture_is_covered() -> None:
    covered = {*EXPECTED, *NOTHING, *DIFFERENT}
    for server in SERVERS:
        names = {p.stem for p in (PLANS / server).glob("*.json")}
        assert names == covered, (server, names ^ covered)


@pytest.mark.parametrize("server", SERVERS)
def test_the_fixtures_record_the_servers(server: str) -> None:
    document = json.loads((PLANS / server / "eq_unindexed.json").read_text())
    assert document["server"].startswith(("8.", "11."))
    assert document["explain_json"]  # EXPLAIN FORMAT=JSON was captured too


def test_the_alembic_call_and_the_join_buffer_note() -> None:
    (issue,) = analyse("mysql8", "self_join")
    assert issue.node == "full table scan (type ALL, in a join buffer)"
    assert issue.advice is not None
    assert issue.advice.alembic == (
        'op.create_index("ix_orders_customer_id", "orders", ["customer_id"])'
    )
    assert issue.condition == "b.customer_id = a.customer_id"


def test_an_or_condition_is_shown_with_or() -> None:
    (issue,) = analyse("mysql8", "or_two_columns")
    assert issue.condition == "customer_id = ? OR total = ?"
    assert issue.advice is None


def test_a_function_of_the_column_asks_for_an_expression_index() -> None:
    (issue,) = analyse("mariadb11", "function_of_column")
    assert issue.expression
    assert issue.column is None


# ------------------------------------------------------------ synthetic rows


def _catalog(**leading: list[str]) -> dict[str, Any]:
    return {
        "orders": {
            "table": "orders",
            "columns": ["id", "customer_id", "total"],
            "leading": leading.get("orders", ["id"]),
        }
    }


def _row(**values: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": 1,
        "select_type": "SIMPLE",
        "table": "orders",
        "type": "ALL",
        "possible_keys": None,
        "key": None,
        "Extra": "Using where",
    }
    return {**base, **values}


SQL = "SELECT id FROM orders WHERE customer_id = %s"


def run(rows: list[dict[str, Any]], sql: str = SQL, **leading: list[str]) -> int:
    return len(mysql_index.analyse(rows, sql, FixtureSchema(_catalog(**leading))))


def test_a_plain_scan_is_reported() -> None:
    assert run([_row()]) == 1


def test_a_scan_on_an_indexed_column_is_the_planners_choice() -> None:
    rows = [_row(possible_keys="ix_customer")]
    assert run(rows, orders=["id", "customer_id"]) == 0


def test_a_scan_although_another_column_leads_an_index() -> None:
    # The index on customer_id exists; the unindexed total is what is missing.
    sql = "SELECT id FROM orders WHERE customer_id = %s AND total = %s"
    assert run([_row()], sql, orders=["id", "customer_id"]) == 1


@pytest.mark.parametrize(
    "plan",
    [
        {"type": "ref"},
        {"type": "eq_ref"},
        {"type": "const"},
        {"type": "system"},
        {"type": "range"},
        {"type": "index_merge"},
        {"type": "fulltext"},
        {"type": "unique_subquery"},
        {"type": "ref_or_null"},
        {"type": "something_new"},  # a plan shape from a future release
        {"type": None},
        {"table": None},  # Impossible WHERE
        {"table": "<derived2>"},
        {"table": "<union1,2>", "select_type": "UNION RESULT"},
    ],
)
def test_unknown_or_indexed_access_gives_no_finding(plan: dict[str, Any]) -> None:
    assert run([_row(**plan)]) == 0


def test_rows_without_the_columns_are_dropped() -> None:
    assert run([{"nothing": "useful"}, {}]) == 0
    assert run([]) == 0


def test_a_scan_with_nothing_to_filter_is_not_reported() -> None:
    assert run([_row()], "SELECT id FROM orders") == 0


def test_an_index_scan_needs_an_equality_on_a_column_outside_the_index() -> None:
    row = _row(type="index", key="PRIMARY")
    assert run([row]) == 1
    ranged = "SELECT id FROM orders WHERE customer_id > %s"
    assert run([row], ranged) == 0  # pagination walks an index on purpose
    assert run([row], orders=["id", "customer_id"]) == 0


def test_column_names_of_the_plan_are_matched_in_any_case() -> None:
    row = {k.upper(): v for k, v in _row().items()}
    assert run([row]) == 1


def test_a_table_used_twice_with_an_index_search_is_not_blamed() -> None:
    rows = [_row(), _row(id=2, type="ref")]
    assert run(rows) == 0


def test_a_cte_hides_a_table_of_the_same_name() -> None:
    sql = (
        "WITH orders AS (SELECT 1 AS customer_id) "
        "SELECT * FROM orders WHERE customer_id = %s"
    )
    assert run([_row()], sql) == 0


# ----------------------------------------------------------- statement reader


def test_the_tokenizer_reads_mysql_quoting_and_comments() -> None:
    sql = "SELECT `a``b`, 'it\\'s' /* c */ FROM t # trailing\nWHERE x = %s -- end"
    texts = [t.text for t in mysql_index.tokenize(sql)]
    assert texts == [
        "SELECT", "a`b", ",", "'it\\'s'", "FROM", "t", "WHERE", "x", "=", "%s",
    ]  # fmt: skip


def test_display_sql_shows_placeholders_as_question_marks() -> None:
    assert mysql_index.display_sql("a = %s AND b = %(x)s AND c LIKE '%%y'") == (
        "a = ? AND b = ? AND c LIKE '%y'"
    )


@pytest.mark.parametrize(
    ("name", "quoted"),
    [
        ("orders", "orders"),
        ("Orders", "`Orders`"),
        ("order", "`order`"),
        ("key", "`key`"),
        ("a b", "`a b`"),
        ("a`b", "`a``b`"),
        ("naïve", "`naïve`"),
    ],
)
def test_quote_name(name: str, quoted: str) -> None:
    assert mysql_index.quote_name(name) == quoted


def test_a_hint_between_table_and_where_does_not_hide_the_table() -> None:
    sql = "SELECT id FROM orders USE INDEX FOR JOIN (a, b) WHERE customer_id = %s"
    assert run([_row()], sql) == 1


# ------------------------------------------------------------------- catalog


class FakeCursor:
    """Answers the catalog queries of Schema from canned rows."""

    def __init__(self, show_index: list[tuple[Any, ...]], names: list[str]) -> None:
        self.show_index = show_index
        self.names = names
        self.description: Any = None
        self.rows: list[Any] = []
        self.sql: list[str] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.sql.append(sql)
        if sql.startswith("SHOW INDEX"):
            self.description = [(n,) for n in self.names]
            self.rows = self.show_index
        elif "information_schema.tables" in sql:
            self.description = [("table_name",), ("table_type",)]
            known = {("orders",): "BASE TABLE", ("v",): "VIEW"}
            self.rows = [(params[0], known[params])] if params in known else []
        elif "information_schema.columns" in sql:
            self.description = [("column_name",)]
            self.rows = [("id",), ("customer_id",)]
        else:
            self.description = [("DATABASE()",)]
            self.rows = [("qg",)]

    def fetchall(self) -> list[Any]:
        return self.rows


MYSQL_SHOW = [
    "Table", "Non_unique", "Key_name", "Seq_in_index", "Column_name",
    "Index_type", "Visible", "Expression",
]  # fmt: skip
MARIA_SHOW = [
    "Table", "Non_unique", "Key_name", "Seq_in_index", "Column_name",
    "Index_type", "Ignored",
]  # fmt: skip


def test_leading_columns_skip_unusable_indexes() -> None:
    rows = [
        ("orders", 0, "PRIMARY", 1, "id", "BTREE", "YES", None),
        ("orders", 1, "ix_a", 1, "customer_id", "BTREE", "NO", None),  # invisible
        ("orders", 1, "ix_b", 2, "total", "BTREE", "YES", None),  # not leading
        ("orders", 1, "ft", 1, "body", "FULLTEXT", "YES", None),
        ("orders", 1, "sp", 1, "geo", "SPATIAL", "YES", None),
        ("orders", 1, "fn", 1, None, "BTREE", "YES", "(lower(`x`))"),  # functional
        ("orders", 1, "ix_c", 1, "Status", "BTREE", "YES", None),
    ]
    schema = mysql_index.Schema(FakeCursor(rows, MYSQL_SHOW))
    assert schema.leading("orders") == {"id", "status"}


def test_a_mariadb_ignored_index_does_not_count() -> None:
    rows = [
        ("orders", 1, "ix_a", 1, "customer_id", "BTREE", "YES"),
        ("orders", 1, "ix_b", 1, "total", "BTREE", "NO"),
    ]
    schema = mysql_index.Schema(FakeCursor(rows, MARIA_SHOW))
    assert schema.leading("orders") == {"total"}


def test_views_and_unknown_tables_are_not_tables() -> None:
    schema = mysql_index.Schema(FakeCursor([], MYSQL_SHOW))
    assert schema.table("orders") == "orders"
    assert schema.table("v") is None
    assert schema.table("missing") is None
    assert schema.database() == "qg"


def test_the_table_name_is_quoted_in_show_index() -> None:
    cursor = FakeCursor([], MYSQL_SHOW)
    mysql_index.Schema(cursor).leading("we`ird")
    assert cursor.sql == ["SHOW INDEX FROM `we``ird`"]


def test_an_unbuffered_user_cursor_is_left_alone() -> None:
    class Result:
        unbuffered_active = True

    class Cursor:
        _result = Result()

    class Adapted:  # SQLAlchemy's async adapter keeps the driver cursor here
        _cursor = Cursor()

    assert mysql_index._unbuffered(Cursor())
    assert mysql_index._unbuffered(Adapted())
    assert not mysql_index._unbuffered(object())
