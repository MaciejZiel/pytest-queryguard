"""Capture real EXPLAIN plans for the plan-parsing fixtures.

    QUERYGUARD_PG_URL=postgresql+pg8000://postgres:pg@localhost:5432/postgres \
        uv run python scripts/capture_plans.py

Writes tests/fixtures/plans/pg<major>/<name>.json for the server the URL points
at. PostgreSQL 16 and newer plan with EXPLAIN (GENERIC_PLAN); older servers
plan a prepared statement with plan_cache_mode = force_generic_plan, which
prints the same $n parameters. Sequential scans are disabled, as in the check.
The statements run in a scratch database that is dropped again.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

SCHEMA = """
CREATE TABLE customers (
    id serial PRIMARY KEY, email varchar(100) NOT NULL, name varchar(100));
CREATE TABLE orders (
    id serial PRIMARY KEY, customer_id integer, status varchar(20), total numeric,
    created_at timestamp, tags text[], data jsonb, note text, kind char(3));
CREATE TABLE order_items (order_id integer, product_id integer, qty integer);
CREATE INDEX order_items_order_product ON order_items (order_id, product_id);
CREATE TABLE "Mixed Case" (id serial PRIMARY KEY, "customerId" integer, "Name" text);
CREATE TABLE archive (id integer, deleted_at timestamp, owner_id integer);
CREATE INDEX archive_live ON archive (owner_id) WHERE deleted_at IS NULL;
CREATE INDEX archive_id ON archive (id);
INSERT INTO customers SELECT g, 'c' || g || '@x.test', 'n' || g
    FROM generate_series(1, 50) g;
INSERT INTO orders SELECT g, g % 50, 'new', g, now(), '{a,b}', '{"k": 1}', 'x', 'abc'
    FROM generate_series(1, 500) g;
INSERT INTO order_items SELECT g % 500, g % 40, 1 FROM generate_series(1, 2000) g;
INSERT INTO "Mixed Case" SELECT g, g % 10, 'n' FROM generate_series(1, 100) g;
INSERT INTO archive SELECT g, NULL, g % 20 FROM generate_series(1, 100) g;
ANALYZE;
"""

# name -> (statement with $n, example arguments for EXECUTE)
QUERIES: dict[str, tuple[str, str]] = {
    "eq_param": ("SELECT * FROM orders WHERE customer_id = $1", "1"),
    "eq_any_array": (
        "SELECT * FROM orders WHERE customer_id = ANY ($1::int[])",
        "'{1,2}'",
    ),
    "eq_in_list": ("SELECT * FROM orders WHERE customer_id IN ($1, $2)", "1, 2"),
    "varchar_cast": ("SELECT * FROM orders WHERE status = $1", "'new'"),
    "char_cast": ("SELECT * FROM orders WHERE kind = $1", "'abc'"),
    "range_two_sided": (
        "SELECT * FROM orders WHERE total > $1 AND total < $2",
        "10, 20",
    ),
    "range_one_sided": ("SELECT * FROM orders WHERE total >= $1", "10"),
    "eq_and_range": (
        "SELECT * FROM orders WHERE customer_id = $1 AND total > $2",
        "1, 5",
    ),
    "two_equalities": (
        "SELECT * FROM orders WHERE customer_id = $1 AND status = $2",
        "1, 'new'",
    ),
    "join_orders_customers": (
        (
            "SELECT * FROM customers c JOIN orders o ON o.customer_id = c.id "
            "WHERE c.id = $1"
        ),
        "1",
    ),
    "left_join_alias": (
        (
            "SELECT * FROM customers LEFT JOIN orders o2 ON o2.customer_id = customers.id "
            "WHERE customers.email = $1"
        ),
        "'c1@x.test'",
    ),
    "function_lower": (
        "SELECT * FROM customers WHERE lower(name) = $1",
        "'n1'",
    ),
    "cast_date": (
        "SELECT * FROM orders WHERE created_at::date = $1",
        "'2026-01-01'",
    ),
    "like": ("SELECT * FROM customers WHERE name LIKE $1", "'n1%'"),
    "is_null": ("SELECT * FROM orders WHERE note IS NULL", ""),
    "not_equal": ("SELECT * FROM orders WHERE status <> $1", "'new'"),
    "array_any": ("SELECT * FROM orders WHERE $1 = ANY (tags)", "'a'"),
    "json_arrow": ("SELECT * FROM orders WHERE data ->> 'k' = $1", "'1'"),
    "or_two_columns": (
        "SELECT * FROM orders WHERE customer_id = $1 OR status = $2",
        "1, 'new'",
    ),
    "trailing_index_column": (
        "SELECT * FROM order_items WHERE product_id = $1",
        "1",
    ),
    "leading_index_column": (
        "SELECT * FROM order_items WHERE order_id = $1 AND product_id = $2",
        "1, 1",
    ),
    "whole_index_order_limit": (
        "SELECT * FROM orders WHERE customer_id = $1 ORDER BY id LIMIT 10",
        "1",
    ),
    "order_by_no_filter": ("SELECT * FROM orders ORDER BY id LIMIT 10", ""),
    "quoted_identifiers": (
        'SELECT * FROM "Mixed Case" m WHERE m."customerId" = $1',
        "1",
    ),
    "quoted_alias": (
        'SELECT * FROM "Mixed Case" AS "M x" WHERE "M x"."customerId" = $1',
        "1",
    ),
    "initplan": (
        (
            "SELECT * FROM orders WHERE customer_id = "
            "(SELECT id FROM customers WHERE email = $1)"
        ),
        "'c1@x.test'",
    ),
    "in_subquery": (
        (
            "SELECT * FROM customers WHERE id IN "
            "(SELECT customer_id FROM orders WHERE total > $1)"
        ),
        "10",
    ),
    "correlated_subplan": (
        (
            "SELECT (SELECT count(*) FROM orders o WHERE o.customer_id = c.id) "
            "FROM customers c"
        ),
        "",
    ),
    "partial_index_match": (
        "SELECT * FROM archive WHERE owner_id = $1 AND deleted_at IS NULL",
        "1",
    ),
    "partial_index_other_column": ("SELECT * FROM archive WHERE owner_id = $1", "1"),
    "coalesce": ("SELECT * FROM orders WHERE coalesce(status, 'x') = $1", "'new'"),
    "not_condition": ("SELECT * FROM orders WHERE NOT (status = $1)", "'new'"),
    "cte": (
        "WITH x AS (SELECT * FROM orders WHERE customer_id = $1) SELECT * FROM x",
        "1",
    ),
    "group_by_filter": (
        "SELECT status, count(*) FROM orders WHERE customer_id = $1 GROUP BY status",
        "1",
    ),
    "order_by_with_equality": (
        "SELECT * FROM orders WHERE customer_id = $1 ORDER BY created_at DESC",
        "1",
    ),
    "literal_constant": ("SELECT * FROM orders WHERE status = 'new'", ""),
}


def explain(conn, major: int, statement: str, args: str) -> object:
    conn.execute(text("SET enable_seqscan = off"))
    if major >= 16:
        sql = "EXPLAIN (GENERIC_PLAN, VERBOSE, FORMAT JSON) " + statement
        return conn.exec_driver_sql(sql).scalar()
    conn.execute(text("SET plan_cache_mode = force_generic_plan"))
    conn.exec_driver_sql("DEALLOCATE ALL")
    conn.exec_driver_sql("PREPARE q AS " + statement)
    call = f"EXECUTE q({args})" if args else "EXECUTE q"
    return conn.exec_driver_sql(f"EXPLAIN (VERBOSE, FORMAT JSON) {call}").scalar()


def main() -> None:
    url = make_url(os.environ["QUERYGUARD_PG_URL"])
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text("DROP DATABASE IF EXISTS qg_plans"))
        conn.execute(text("CREATE DATABASE qg_plans"))
    engine = create_engine(url.set(database="qg_plans"), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            major = conn.dialect.server_version_info[0]  # type: ignore[index]
            for statement in SCHEMA.split(";\n"):
                if statement.strip():
                    conn.exec_driver_sql(statement)
            out = Path(__file__).parents[1] / "tests" / "fixtures" / "plans"
            out = out / f"pg{major}"
            out.mkdir(parents=True, exist_ok=True)
            for name, (statement, args) in QUERIES.items():
                plan = explain(conn, major, statement, args)
                if isinstance(plan, str):
                    plan = json.loads(plan)
                document = {"query": statement, "plan": plan}
                (out / f"{name}.json").write_text(
                    json.dumps(document, indent=1) + "\n", encoding="utf-8"
                )
            print(f"PostgreSQL {major}: {len(QUERIES)} plans in {out}")
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text("DROP DATABASE IF EXISTS qg_plans WITH (FORCE)"))
        admin.dispose()


if __name__ == "__main__":
    if "QUERYGUARD_PG_URL" not in os.environ:
        sys.exit("set QUERYGUARD_PG_URL")
    main()
