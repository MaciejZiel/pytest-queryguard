"""Baseline labels that do not depend on the database or the SQLAlchemy series.

A ``repeated-statement`` or ``write-loop`` label is SQL as the driver got it, and
a ``missing-index`` label may name a schema; the baseline keeps them without
schema qualifiers, quotes, placeholder style and SQLAlchemy 2.0's column
aliases, and still reads a file written in the full form before 1.1.0.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pytest_queryguard import baseline
from pytest_queryguard.baseline import portable, portable_sql, portable_table

pytestmark = pytest.mark.allow_nplusone

MODELS = (Path(__file__).parent / "models.py").read_text()
OLD_FILE = (
    Path(__file__).parent
    / "fixtures"
    / "baseline"
    / ("v1-postgresql-sqlalchemy-2.0.json")
)

SQLITE_21 = "SELECT customers.id, customers.name FROM customers WHERE customers.id = ?"


@pytest.mark.parametrize(
    "sql",
    [
        SQLITE_21,
        # SQLAlchemy 2.0 labels each ORM column
        (
            "SELECT customers.id AS customers_id, customers.name AS customers_name "
            "FROM customers WHERE customers.id = ?"
        ),
        # psycopg, pg8000 / MySQL drivers, asyncpg, oracledb
        (
            "SELECT customers.id, customers.name FROM customers "
            "WHERE customers.id = %(id_1)s"
        ),
        "SELECT customers.id, customers.name FROM customers WHERE customers.id = %s",
        "SELECT customers.id, customers.name FROM customers WHERE customers.id = $1",
        "SELECT customers.id, customers.name FROM customers WHERE customers.id = :id_1",
        # a PostgreSQL cast on the parameter
        (
            "SELECT customers.id, customers.name FROM customers "
            "WHERE customers.id = %(id_1)s::INTEGER"
        ),
        # a schema (Table(schema=...) or schema_translate_map), with 2.0 aliases
        (
            "SELECT shop.customers.id AS shop_customers_id, shop.customers.name AS "
            "shop_customers_name FROM shop.customers WHERE shop.customers.id = %(id_1)s"
        ),
        # quoted names: standard and MySQL
        (
            'SELECT "shop"."customers"."id", "shop"."customers"."name" FROM '
            '"shop"."customers" WHERE "shop"."customers"."id" = %s'
        ),
        (
            "SELECT `customers`.`id`, `customers`.`name` FROM `shop`.`customers` "
            "WHERE `customers`.`id` = %s"
        ),
    ],
)
def test_one_statement_has_one_label_everywhere(sql: str) -> None:
    assert portable_sql(sql) == SQLITE_21


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (
            (
                "INSERT INTO shop.orders (customer_id) VALUES (%(customer_id)s) "
                "RETURNING shop.orders.id"
            ),
            "INSERT INTO orders (customer_id) VALUES (?) RETURNING orders.id",
        ),
        (
            "UPDATE shop.orders SET note=%(note)s WHERE shop.orders.id = %(id_1)s",
            "UPDATE orders SET note=? WHERE orders.id = ?",
        ),
        (
            "DELETE FROM `shop`.`orders` WHERE `orders`.`id` = %s",
            "DELETE FROM orders WHERE orders.id = ?",
        ),
        # a schema named like a table: only the qualifier goes
        ("SELECT auth.auth.id FROM auth.auth", "SELECT auth.id FROM auth"),
        # a FROM list: the schema is known from the first table
        (
            "SELECT a.x FROM s.a, s.b WHERE s.b.y = ?",
            "SELECT a.x FROM a, b WHERE b.y = ?",
        ),
        # aliases the code wrote are kept apart from 2.0's column labels
        (
            (
                "SELECT count(*) AS n FROM orders JOIN customers AS customers_1 "
                "ON customers_1.id = orders.customer_id LIMIT ? OFFSET ?"
            ),
            (
                "SELECT count(*) AS n FROM orders JOIN customers AS customers_1 "
                "ON customers_1.id = orders.customer_id LIMIT ? OFFSET ?"
            ),
        ),
        # FROM in IS DISTINCT FROM and in a function is not before a table
        (
            "SELECT o.x FROM orders AS o WHERE o.a IS DISTINCT FROM o.b",
            "SELECT o.x FROM orders AS o WHERE o.a IS DISTINCT FROM o.b",
        ),
        (
            "SELECT EXTRACT(year FROM orders.created) FROM shop.orders",
            "SELECT EXTRACT(year FROM orders.created) FROM orders",
        ),
        # but a subquery's FROM is
        (
            "SELECT anon_1.id FROM (SELECT c.id FROM shop.c) AS anon_1",
            "SELECT anon_1.id FROM (SELECT c.id FROM c) AS anon_1",
        ),
        # an alias named like the schema keeps its columns
        ("SELECT s.x FROM s.t AS s", "SELECT s.x FROM t AS s"),
        # 2.0 reads a subquery's columns by their labels, 2.1 by their names
        (
            (
                "SELECT anon_1.customers_id FROM (SELECT customers.id AS customers_id "
                "FROM customers) AS anon_1"
            ),
            "SELECT anon_1.id FROM (SELECT customers.id FROM customers) AS anon_1",
        ),
        # only SQLAlchemy's labels go, not CAST(... AS type) or the code's own
        (
            "SELECT CAST(orders.total AS NUMERIC), orders.total AS revenue FROM orders",
            "SELECT CAST(orders.total AS NUMERIC), orders.total AS revenue FROM orders",
        ),
        # casts of more than one word
        (
            (
                "SELECT a.ts FROM a WHERE a.ts = $1::TIMESTAMP WITHOUT TIME ZONE "
                "AND a.n = %(n)s::NUMERIC(?, ?) AND a.d = %(d)s::DOUBLE PRECISION"
            ),
            "SELECT a.ts FROM a WHERE a.ts = ? AND a.n = ? AND a.d = ?",
        ),
        # a quoted name that is not a plain name keeps its quotes
        ('SELECT "a""b".x, "c d".y FROM t', 'SELECT "a""b".x, "c d".y FROM t'),
        # a function in a schema is not a table
        (
            "SELECT pg_catalog.lower(name) FROM t",
            "SELECT pg_catalog.lower(name) FROM t",
        ),
    ],
)
def test_portable_sql(sql: str, expected: str) -> None:
    assert portable_sql(sql) == expected
    assert portable_sql(expected) == expected  # applying it again changes nothing


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("orders", "orders"),
        ("shop.orders", "orders"),
        ('"shop"."Orders"', "Orders"),
        ('"we.ird"', "we.ird"),
        ('shop."user"', "user"),
    ],
)
def test_portable_table(label: str, expected: str) -> None:
    assert portable_table(label) == expected


def test_only_sql_and_table_labels_change() -> None:
    lazy = ("relationship-load", "Order.customer", "a.py", "f")
    assert portable(lazy) == lazy
    assert portable(("missing-index", "shop.orders", None, None)) == (
        "missing-index",
        "orders",
        None,
        None,
    )
    assert portable(("write-loop", "INSERT INTO s.t (a) VALUES (%s)", "a", "f"))[1] == (
        "INSERT INTO t (a) VALUES (?)"
    )


def test_a_file_written_before_1_1_reads_into_the_portable_form() -> None:
    identities = baseline.load(OLD_FILE)
    assert identities == {
        ("missing-index", "customers", "test_a.py", "test_repeat"),
        ("relationship-load", "Order.customer", "test_a.py", "test_loop"),
        ("repeated-statement", SQLITE_21, "test_a.py", "test_repeat"),
        (
            "write-loop",
            "INSERT INTO customers (id, name) VALUES (?, ?)",
            "test_a.py",
            "test_writes",
        ),
    }


def test_dumps_writes_the_portable_form() -> None:
    old = ("repeated-statement", OLD_LABEL, "test_a.py", "test_repeat")
    entries = json.loads(baseline.dumps([old]))["findings"]
    assert entries == [
        {
            "kind": "repeated-statement",
            "label": SQLITE_21,
            "file": "test_a.py",
            "function": "test_repeat",
        }
    ]


OLD_LABEL = json.loads(OLD_FILE.read_text())["findings"][2]["label"]

REPEAT = """
from sqlalchemy import select
from qgmodels import Customer, Order, make_sync_session


def test_repeat():
    session = make_sync_session()
    for customer_id in (1, 2, 3, 1, 2):
        session.execute(select(Customer).where(Customer.id == customer_id)).all()


def test_loop():
    session = make_sync_session()
    for order in session.query(Order).all():
        _ = order.customer.name
"""


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makepyfile(qgmodels=MODELS, test_a=REPEAT)
    return pytester


def test_an_old_postgresql_baseline_matches_on_sqlite(project: pytest.Pytester) -> None:
    (project.path / "qg.json").write_text(OLD_FILE.read_text())
    result = project.runpytest("--queryguard-baseline=qg.json")
    result.assert_outcomes(passed=2)
    out = result.stdout.str()
    assert "N+1 detected" not in out
    # the two entries this project does not produce are the only stale ones
    result.stdout.fnmatch_lines(
        ["2 baseline entries in *qg.json matched no finding in this run:"]
    )
    assert "repeated-statement" not in out.split("stale baseline entries")[1]


def test_an_update_of_an_old_file_writes_the_portable_form_without_churn(
    project: pytest.Pytester,
) -> None:
    path = project.path / "qg.json"
    path.write_text(OLD_FILE.read_text())
    result = project.runpytest(
        "--queryguard-baseline=qg.json", "--queryguard-update-baseline"
    )
    result.assert_outcomes(passed=2)
    # the repeat and the lazy load stay; the two this project lacks go
    result.stdout.fnmatch_lines(["*2 entries (0 added, 2 removed)"])
    labels = {e["label"] for e in json.loads(path.read_text())["findings"]}
    assert labels == {"Order.customer", SQLITE_21}
    again = project.runpytest("--queryguard-baseline=qg.json")
    again.assert_outcomes(passed=2)
    again.stdout.no_fnmatch_line("*stale baseline*")


def test_the_json_report_marks_an_old_entry_as_baselined(
    project: pytest.Pytester,
) -> None:
    (project.path / "qg.json").write_text(OLD_FILE.read_text())
    project.runpytest("--queryguard-baseline=qg.json", "--queryguard-json=out.json")
    tests = json.loads((project.path / "out.json").read_text())["tests"]
    findings = [f for test in tests.values() for f in test["findings"]]
    assert findings
    assert all(f["baselined"] for f in findings)
    # the report keeps the label as the finding has it
    labels = [f["label"] for f in findings]
    assert any(portable_sql(label) == SQLITE_21 for label in labels)
