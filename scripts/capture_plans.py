"""Capture real EXPLAIN plans for the plan-parsing fixtures.

    QUERYGUARD_PG_URL=postgresql+pg8000://postgres:pg@localhost:5432/postgres \
        uv run python scripts/capture_plans.py

Writes tests/fixtures/plans/pg<major>/<name>.json for the server the URL points
at. PostgreSQL 16 and newer plan with EXPLAIN (GENERIC_PLAN); older servers
plan a prepared statement with plan_cache_mode = force_generic_plan, which
prints the same $n parameters. Sequential scans are disabled, as in the check.
The statements run in a scratch database that is dropped again (and a role,
qg_plans_tenant, for the row level security scenario, dropped as well).

Each fixture has the statement, the plan, what the check reads from the
catalog for that plan (the same lookups as indexes.py) and, on 16 and newer,
what run_explain() reported on that server (``live``), so that
tests/test_plan_fixtures.py can replay the check offline and compare. Capture
every version after adding a scenario: the tests expect the same names in
each directory. The fixtures come from the official images:

    docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=pg postgres:<major>
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from pytest_queryguard import indexes
from pytest_queryguard.events import Catalog

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
CREATE TABLE posts (
    id serial PRIMARY KEY, author_id integer, title text, status text,
    draft boolean, score integer, published_at timestamptz, tags text[]);
CREATE INDEX posts_lower_title ON posts (lower(title));
CREATE INDEX posts_tags ON posts USING gin (tags);
CREATE INDEX posts_author_published ON posts (author_id, published_at DESC NULLS LAST);
CREATE INDEX posts_published_score ON posts (score) WHERE status = 'published';
CREATE TABLE docs (id serial PRIMARY KEY, owner_id integer, body jsonb);
CREATE INDEX docs_body ON docs USING gin (body);
CREATE TABLE categories (id serial PRIMARY KEY, parent_id integer, name text);
CREATE TABLE events (
    id integer, account_id integer, kind text, created_at timestamp NOT NULL,
    payload jsonb) PARTITION BY RANGE (created_at);
CREATE TABLE events_2025 PARTITION OF events
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE events_2026 PARTITION OF events
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE INDEX events_account ON events (account_id);
CREATE TABLE vehicles (id serial PRIMARY KEY, owner_id integer, plate text);
CREATE TABLE cars (seats integer) INHERITS (vehicles);
CREATE TABLE trucks (payload integer) INHERITS (vehicles);
CREATE SCHEMA billing;
CREATE TABLE billing.invoices (id serial PRIMARY KEY, customer_id integer, amount numeric);
CREATE SCHEMA "Sales";
CREATE TABLE "Sales"."Order Lines" (id serial PRIMARY KEY, "orderId" integer, "Qty" integer);
CREATE UNLOGGED TABLE cache_entries (key text, value text, expires_at timestamp);
CREATE TEMP TABLE scratch (id integer, ref integer);
CREATE TABLE tenant_rows (id integer, tenant_id integer, secret text);
ALTER TABLE tenant_rows ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_only ON tenant_rows
    USING (tenant_id = current_setting('app.tenant', true)::integer);
GRANT SELECT ON tenant_rows TO qg_plans_tenant;
INSERT INTO posts SELECT g, g % 30, 't' || g, CASE WHEN g % 3 = 0 THEN 'published'
    ELSE 'draft' END, g % 2 = 0, g, now(), '{a,b}' FROM generate_series(1, 300) g;
INSERT INTO docs SELECT g, g % 10, jsonb_build_object('k', g, 'a', jsonb_build_object('b', g))
    FROM generate_series(1, 200) g;
INSERT INTO categories SELECT g, nullif(g / 3, 0), 'c' || g FROM generate_series(1, 100) g;
INSERT INTO events SELECT g, g % 40, 'k' || g % 5,
    timestamp '2025-06-01' + g * interval '1 day', '{}' FROM generate_series(1, 400) g;
INSERT INTO cars SELECT g, g % 20, 'p' || g, 4 FROM generate_series(1, 100) g;
INSERT INTO trucks SELECT g + 100, g % 20, 't' || g, 10 FROM generate_series(1, 100) g;
INSERT INTO billing.invoices SELECT g, g % 50, g FROM generate_series(1, 200) g;
INSERT INTO "Sales"."Order Lines" SELECT g, g % 30, 1 FROM generate_series(1, 100) g;
INSERT INTO cache_entries SELECT 'k' || g, 'v', now() FROM generate_series(1, 100) g;
INSERT INTO scratch SELECT g, g % 10 FROM generate_series(1, 100) g;
INSERT INTO tenant_rows SELECT g, g % 5, 's' FROM generate_series(1, 100) g;
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
    # ---- more predicate shapes
    "between": ("SELECT * FROM orders WHERE total BETWEEN $1 AND $2", "1, 9"),
    "is_not_null": ("SELECT * FROM orders WHERE note IS NOT NULL", ""),
    "is_distinct_from": (
        "SELECT * FROM orders WHERE status IS DISTINCT FROM $1",
        "'new'",
    ),
    "not_in_list": ("SELECT * FROM orders WHERE customer_id NOT IN ($1, $2)", "1, 2"),
    "ne_all_array": (
        "SELECT * FROM orders WHERE customer_id <> ALL ($1::int[])",
        "'{1,2}'",
    ),
    "in_list_literals": (
        "SELECT * FROM orders WHERE status IN ('new', 'paid')",
        "",
    ),
    "any_array_literal": (
        "SELECT * FROM orders WHERE customer_id = ANY ('{1,2}'::int[])",
        "",
    ),
    "any_array_pk": ("SELECT * FROM orders WHERE id = ANY ($1::int[])", "'{1,2}'"),
    "or_same_column": (
        "SELECT * FROM orders WHERE customer_id = $1 OR customer_id = $2",
        "1, 2",
    ),
    "ilike": ("SELECT * FROM customers WHERE name ILIKE $1", "'n1%'"),
    "like_literal_prefix": ("SELECT * FROM customers WHERE name LIKE 'n1%'", ""),
    "regex_match": ("SELECT * FROM customers WHERE name ~ $1", "'^n'"),
    "similar_to": ("SELECT * FROM customers WHERE name SIMILAR TO $1", "'n%'"),
    "boolean_column": ("SELECT * FROM posts WHERE draft", ""),
    "boolean_not": ("SELECT * FROM posts WHERE NOT draft AND author_id = $1", "1"),
    "boolean_is_true": ("SELECT * FROM posts WHERE draft IS TRUE", ""),
    "arithmetic": ("SELECT * FROM orders WHERE total * 2 > $1", "10"),
    "date_trunc": (
        "SELECT * FROM orders WHERE date_trunc('day', created_at) = $1",
        "'2026-01-01'",
    ),
    "extract_year": (
        "SELECT * FROM orders WHERE extract(year FROM created_at) = $1",
        "2026",
    ),
    "at_time_zone": (
        "SELECT * FROM orders WHERE (created_at AT TIME ZONE 'UTC') > $1",
        "'2026-01-01'",
    ),
    "now_minus_interval": (
        "SELECT * FROM orders WHERE created_at > now() - interval '1 day'",
        "",
    ),
    "collate": (
        'SELECT * FROM customers WHERE name = $1 COLLATE "C"',
        "'n1'",
    ),
    "numeric_param_cast": ("SELECT * FROM orders WHERE total = $1::numeric", "1"),
    "concat": ("SELECT * FROM customers WHERE (name || email) = $1", "'x'"),
    "case_expression": (
        ("SELECT * FROM orders WHERE CASE WHEN status = 'new' THEN 1 ELSE 0 END = $1"),
        "1",
    ),
    "row_comparison": (
        (
            "SELECT * FROM orders WHERE (customer_id, total) > ($1, $2) "
            "ORDER BY customer_id, total LIMIT 10"
        ),
        "1, 1",
    ),
    "text_search": (
        "SELECT * FROM orders WHERE to_tsvector('simple', note) @@ to_tsquery('simple', $1)",
        "'x'",
    ),
    "nullif": ("SELECT * FROM orders WHERE nullif(status, '') = $1", "'new'"),
    "array_subscript": ("SELECT * FROM orders WHERE tags[1] = $1", "'a'"),
    "greatest": ("SELECT * FROM orders WHERE greatest(total, 0) > $1", "1"),
    "current_date": (
        "SELECT * FROM orders WHERE created_at::date = CURRENT_DATE",
        "",
    ),
    "literal_quote": ("SELECT * FROM orders WHERE note = 'it''s'", ""),
    "literal_backslash": ("SELECT * FROM orders WHERE note LIKE 'a\\_%'", ""),
    "substring": (
        "SELECT * FROM customers WHERE substring(name FROM 1 FOR 2) = $1",
        "'n1'",
    ),
    # ---- ORDER BY, LIMIT, DISTINCT, window functions
    "order_nulls_last_limit": (
        (
            "SELECT * FROM orders WHERE customer_id = $1 "
            "ORDER BY created_at DESC NULLS LAST LIMIT 10"
        ),
        "1",
    ),
    "order_nulls_first_no_filter": (
        "SELECT * FROM orders ORDER BY created_at NULLS FIRST LIMIT 5",
        "",
    ),
    "limit_offset": (
        "SELECT * FROM orders WHERE status = $1 ORDER BY id LIMIT 10 OFFSET 20",
        "'new'",
    ),
    "distinct": (
        "SELECT DISTINCT status FROM orders WHERE customer_id = $1",
        "1",
    ),
    "distinct_on": (
        (
            "SELECT DISTINCT ON (customer_id) * FROM orders WHERE total > $1 "
            "ORDER BY customer_id, created_at DESC"
        ),
        "1",
    ),
    "window_function": (
        (
            "SELECT id, row_number() OVER (PARTITION BY customer_id ORDER BY created_at) "
            "FROM orders WHERE status = $1"
        ),
        "'new'",
    ),
    "group_by_having": (
        (
            "SELECT customer_id, count(*) FROM orders GROUP BY customer_id "
            "HAVING count(*) > $1"
        ),
        "1",
    ),
    "aggregate_filter": (
        "SELECT count(*) FILTER (WHERE status = $1) FROM orders WHERE customer_id = $2",
        "'new', 1",
    ),
    # ---- joins, sub-queries, CTEs, LATERAL
    "exists_semi_join": (
        (
            "SELECT * FROM customers c WHERE EXISTS "
            "(SELECT 1 FROM orders o WHERE o.customer_id = c.id AND o.total > $1)"
        ),
        "1",
    ),
    "not_exists_anti_join": (
        (
            "SELECT * FROM customers c WHERE NOT EXISTS "
            "(SELECT 1 FROM orders o WHERE o.customer_id = c.id)"
        ),
        "",
    ),
    "not_in_subquery": (
        (
            "SELECT * FROM customers WHERE id NOT IN (SELECT customer_id FROM orders "
            "WHERE status = $1)"
        ),
        "'new'",
    ),
    "left_join_is_null": (
        (
            "SELECT c.* FROM customers c LEFT JOIN orders o ON o.customer_id = c.id "
            "WHERE o.id IS NULL"
        ),
        "",
    ),
    "full_join": (
        (
            "SELECT * FROM orders o FULL JOIN customers c ON c.id = o.customer_id "
            "WHERE o.status = $1"
        ),
        "'new'",
    ),
    "join_inequality": (
        "SELECT * FROM orders o JOIN customers c ON o.total > c.id WHERE c.email = $1",
        "'c1@x.test'",
    ),
    "self_join": (
        (
            "SELECT o2.* FROM orders o1 JOIN orders o2 ON o2.customer_id = o1.customer_id "
            "WHERE o1.id = $1"
        ),
        "1",
    ),
    "three_way_join": (
        (
            "SELECT * FROM customers c JOIN orders o ON o.customer_id = c.id "
            "JOIN order_items i ON i.order_id = o.id WHERE c.email = $1"
        ),
        "'c1@x.test'",
    ),
    "values_join": (
        (
            "SELECT * FROM orders o JOIN (VALUES ($1::int), ($2::int)) v(id) "
            "ON o.customer_id = v.id"
        ),
        "1, 2",
    ),
    "unnest_in": (
        "SELECT * FROM orders WHERE customer_id IN (SELECT unnest($1::int[]))",
        "'{1,2}'",
    ),
    "lateral": (
        (
            "SELECT * FROM customers c CROSS JOIN LATERAL (SELECT * FROM orders o "
            "WHERE o.customer_id = c.id ORDER BY o.created_at DESC LIMIT 3) x "
            "WHERE c.email = $1"
        ),
        "'c1@x.test'",
    ),
    "union": (
        (
            "SELECT id FROM orders WHERE status = $1 UNION "
            "SELECT id FROM orders WHERE customer_id = $2"
        ),
        "'new', 1",
    ),
    "subquery_in_from": (
        "SELECT * FROM (SELECT * FROM orders WHERE status = $1) s WHERE s.total > $2",
        "'new', 1",
    ),
    "cte_materialized": (
        (
            "WITH x AS MATERIALIZED (SELECT * FROM orders WHERE customer_id = $1) "
            "SELECT * FROM x WHERE x.total > $2"
        ),
        "1, 1",
    ),
    "cte_recursive": (
        (
            "WITH RECURSIVE t AS (SELECT * FROM categories WHERE id = $1 UNION ALL "
            "SELECT c.* FROM categories c JOIN t ON c.parent_id = t.id) SELECT * FROM t"
        ),
        "1",
    ),
    "cte_data_modifying": (
        (
            "WITH d AS (DELETE FROM orders WHERE status = $1 RETURNING *) "
            "SELECT count(*) FROM d"
        ),
        "'new'",
    ),
    # ---- JSONB and arrays
    "jsonb_contains_gin": (
        "SELECT * FROM docs WHERE body @> $1::jsonb",
        "'{\"k\": 1}'",
    ),
    "jsonb_key_exists_gin": ("SELECT * FROM docs WHERE body ? $1", "'k'"),
    "jsonb_nested_arrow": (
        "SELECT * FROM docs WHERE body -> 'a' ->> 'b' = $1",
        "'1'",
    ),
    "jsonb_arrow_cast": (
        "SELECT * FROM docs WHERE (body ->> 'k')::int > $1",
        "1",
    ),
    "jsonb_contains_unindexed": (
        "SELECT * FROM orders WHERE data @> $1::jsonb",
        "'{\"k\": 1}'",
    ),
    "array_contains_gin": ("SELECT * FROM posts WHERE tags @> ARRAY[$1]", "'a'"),
    "array_overlap_gin": ("SELECT * FROM posts WHERE tags && $1::text[]", "'{a}'"),
    "array_contains_unindexed": (
        "SELECT * FROM orders WHERE tags @> $1::text[]",
        "'{a}'",
    ),
    "array_length": ("SELECT * FROM orders WHERE array_length(tags, 1) > $1", "1"),
    # ---- expression and partial indexes, multicolumn indexes
    "expression_index_match": (
        "SELECT * FROM posts WHERE lower(title) = $1",
        "'t1'",
    ),
    "expression_index_other_function": (
        "SELECT * FROM posts WHERE upper(title) = $1",
        "'T1'",
    ),
    "partial_index_parameter": (
        "SELECT * FROM posts WHERE score = $1 AND status = $2",
        "1, 'published'",
    ),
    "partial_index_literal": (
        "SELECT * FROM posts WHERE score = $1 AND status = 'published'",
        "1",
    ),
    "multicolumn_leading_order": (
        (
            "SELECT * FROM posts WHERE author_id = $1 "
            "ORDER BY published_at DESC NULLS LAST LIMIT 5"
        ),
        "1",
    ),
    "multicolumn_trailing_range": (
        "SELECT * FROM posts WHERE published_at > $1",
        "'2026-01-01'",
    ),
    # ---- partitioned tables and inheritance
    "partition_unindexed": ("SELECT * FROM events WHERE kind = $1", "'k1'"),
    "partition_indexed": ("SELECT * FROM events WHERE account_id = $1", "1"),
    "partition_key_param": (
        "SELECT * FROM events WHERE created_at >= $1 AND kind = $2",
        "'2026-02-01', 'k1'",
    ),
    "partition_key_literal": (
        "SELECT * FROM events WHERE created_at >= '2026-02-01' AND kind = $1",
        "'k1'",
    ),
    "partition_order_limit": (
        "SELECT * FROM events WHERE kind = $1 ORDER BY created_at DESC LIMIT 5",
        "'k1'",
    ),
    "inheritance_parent": ("SELECT * FROM vehicles WHERE owner_id = $1", "1"),
    "inheritance_only": ("SELECT * FROM ONLY vehicles WHERE plate = $1", "'p1'"),
    "inheritance_child": ("SELECT * FROM cars WHERE seats = $1", "4"),
    # ---- UPDATE and DELETE (planned like the check, not checked on PostgreSQL)
    "update_in_subquery": (
        (
            "UPDATE orders SET status = $1 WHERE customer_id IN "
            "(SELECT id FROM customers WHERE email = $2)"
        ),
        "'paid', 'c1@x.test'",
    ),
    "update_from": (
        (
            "UPDATE orders o SET status = $1 FROM customers c "
            "WHERE o.customer_id = c.id AND c.name = $2"
        ),
        "'paid', 'n1'",
    ),
    "delete_in_subquery": (
        (
            "DELETE FROM order_items WHERE order_id IN "
            "(SELECT id FROM orders WHERE status = $1)"
        ),
        "'new'",
    ),
    "delete_using": (
        (
            "DELETE FROM orders o USING customers c "
            "WHERE o.customer_id = c.id AND c.name = $1"
        ),
        "'n1'",
    ),
    # ---- kinds of tables, schemas, names
    "temp_table": ("SELECT * FROM scratch WHERE ref = $1", "1"),
    "unlogged_table": ("SELECT * FROM cache_entries WHERE key = $1", "'k1'"),
    "row_level_security": ("SELECT * FROM tenant_rows WHERE id = $1", "1"),
    "schema_qualified": (
        "SELECT * FROM billing.invoices WHERE customer_id = $1",
        "1",
    ),
    "schema_qualified_join": (
        (
            "SELECT * FROM billing.invoices i JOIN customers c ON c.id = i.customer_id "
            "WHERE i.amount > $1"
        ),
        "1",
    ),
    "mixed_case_schema": (
        'SELECT * FROM "Sales"."Order Lines" WHERE "orderId" = $1',
        "1",
    ),
    "mixed_case_order_by": (
        'SELECT * FROM "Mixed Case" WHERE "customerId" = $1 ORDER BY "Name" LIMIT 3',
        "1",
    ),
}

# Statements planned as another role: a superuser bypasses row level security.
ROLES = {"row_level_security": "qg_plans_tenant"}
ROLE_SETUP = (
    "DO $$BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'qg_plans_tenant') "
    "THEN CREATE ROLE qg_plans_tenant NOLOGIN; END IF; END$$"
)


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


def _scanned_tables(plan: dict[str, Any]) -> set[tuple[str, str]]:
    found = set()
    stack: list[Any] = [plan]
    while stack:
        node = stack.pop()
        if node.get("Relation Name") and node.get("Schema"):
            found.add((node["Schema"], node["Relation Name"]))
        stack.extend(node.get("Plans", []))
    return found


def read_catalog(cursor: Any, major: int, plan: dict[str, Any]) -> dict[str, Any]:
    """What the index check reads from the catalog for this plan, as JSON.

    The same lookups as the check (indexes.py), so that the fixture tests can
    replay find_scans(), the partial-index rule and the advisor offline.
    """
    leading, varchar = indexes._read_catalog(
        cursor, plan, Catalog(), leading=major < indexes.SKIP_SCAN_VERSION
    )
    tables = _scanned_tables(plan)
    partials = indexes._partial_indexes(cursor, tables, None, False)
    lookup = indexes._lookup(indexes._TABLE_INDEXES_LOOKUP, tables, False)
    existing = [
        [str(r[0]), str(r[1]), str(r[2]), str(r[3]), bool(r[4]), list(r[5]), r[6]]
        for r in indexes._rows(cursor, lookup)
    ]
    roots = indexes.partition_roots(cursor, tables, None, False)
    return {
        "roots": sorted([s, t, *root] for (s, t), root in roots.items()),
        "leading": sorted([s, i, c] for (s, i), c in leading.items()),
        "varchar": sorted([s, t, sorted(c)] for (s, t), c in varchar.items()),
        "partial": sorted(
            [p.schema, p.table, p.name, p.predicate, sorted(p.table_columns)]
            for found in partials.values()
            for p in found
        ),
        "indexes": sorted(existing, key=lambda r: (r[0], r[1], r[2])),
    }


def live_findings(dbapi: Any, major: int, statement: str) -> list[dict[str, Any]]:
    """What the check itself reports for the statement on this server (16+)."""
    outcome = indexes.run_explain(
        dbapi,
        statement,
        leading=major < indexes.SKIP_SCAN_VERSION,
        catalog=Catalog(),
        advise=indexes.AdvisorOptions("off", major),
        skip_partial=True,
    )
    if isinstance(outcome, indexes.ExplainError):
        return [{"error": outcome.message}]
    found = list(outcome.issues or ())
    skipped = list(outcome.partial_skipped)
    return [describe(i) | {"partial_skipped": False} for i in found] + [
        describe(i) | {"partial_skipped": True} for i in skipped
    ]


def describe(issue: Any) -> dict[str, Any]:
    advice = issue.advice
    return {
        "schema": issue.schema,
        "table": issue.table,
        "node": issue.node,
        "kind": issue.kind,
        "column": issue.column,
        "expression": issue.expression,
        "create_sql": None if advice is None else advice.create_sql,
        "skipped": None if advice is None else advice.skipped,
    }


def main() -> None:
    url = make_url(os.environ["QUERYGUARD_PG_URL"])
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text("DROP DATABASE IF EXISTS qg_plans"))
        conn.execute(text("CREATE DATABASE qg_plans"))
        conn.exec_driver_sql(ROLE_SETUP)
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
            dbapi = conn.connection.dbapi_connection
            for name, (statement, args) in QUERIES.items():
                role = ROLES.get(name)
                if role:
                    conn.exec_driver_sql(f"SET ROLE {role}")
                plan = explain(conn, major, statement, args)
                if isinstance(plan, str):
                    plan = json.loads(plan)
                cursor = dbapi.cursor()
                catalog = read_catalog(cursor, major, plan[0]["Plan"])
                cursor.close()
                document: dict[str, Any] = {
                    "query": statement,
                    "plan": plan,
                    "catalog": catalog,
                }
                if major >= indexes.MIN_SERVER_VERSION:
                    conn.exec_driver_sql("RESET enable_seqscan")
                    document["live"] = live_findings(dbapi, major, statement)
                if role:
                    conn.exec_driver_sql("RESET ROLE")
                (out / f"{name}.json").write_text(
                    json.dumps(document, indent=1) + "\n", encoding="utf-8"
                )
            print(f"PostgreSQL {major}: {len(QUERIES)} plans in {out}")
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text("DROP DATABASE IF EXISTS qg_plans WITH (FORCE)"))
            conn.exec_driver_sql("DROP ROLE IF EXISTS qg_plans_tenant")
        admin.dispose()


if __name__ == "__main__":
    if "QUERYGUARD_PG_URL" not in os.environ:
        sys.exit("set QUERYGUARD_PG_URL")
    main()
