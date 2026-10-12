"""The schema and statements shared by the MySQL and MariaDB tests.

scripts/capture_mysql_plans.py runs them on a live server to write the plan
fixtures under tests/fixtures/plans/; the live tests run the same schema.
"""

from __future__ import annotations

# Order matters: the DDL comes first, then the rows, then ANALYZE TABLE.
DDL = [
    """CREATE TABLE customers (
        id INT AUTO_INCREMENT PRIMARY KEY,
        email VARCHAR(100) NOT NULL,
        name VARCHAR(100),
        country VARCHAR(2),
        UNIQUE KEY ix_customers_email (email)
    ) ENGINE=InnoDB""",
    """CREATE TABLE orders (
        id INT AUTO_INCREMENT PRIMARY KEY,
        customer_id INT,
        reference VARCHAR(20),
        status VARCHAR(20),
        total INT,
        created DATETIME,
        KEY ix_orders_reference (reference),
        KEY ix_orders_status_created (status, created)
    ) ENGINE=InnoDB""",
    """CREATE TABLE order_items (
        order_id INT NOT NULL,
        product_id INT NOT NULL,
        qty INT,
        KEY ix_order_items_order_product (order_id, product_id)
    ) ENGINE=InnoDB""",
    """CREATE TABLE articles (
        id INT AUTO_INCREMENT PRIMARY KEY,
        title VARCHAR(100),
        body TEXT,
        FULLTEXT KEY ft_articles (title, body)
    ) ENGINE=InnoDB""",
    """CREATE TABLE tiny (
        code VARCHAR(10) NOT NULL,
        label VARCHAR(10),
        KEY ix_tiny_code (code)
    ) ENGINE=InnoDB""",
    "CREATE TABLE tiny_plain (code VARCHAR(10) NOT NULL, label VARCHAR(10)) ENGINE=InnoDB",
    """CREATE VIEW v_open_orders AS
        SELECT id, customer_id, total FROM orders WHERE status = 'open'""",
]
TABLES = ("customers", "orders", "order_items", "articles", "tiny", "tiny_plain")
ROWS = 3000


def fill(cursor: object) -> None:
    """Insert deterministic rows with a plain DB-API cursor."""
    execute = cursor.execute  # type: ignore[attr-defined]
    executemany = cursor.executemany  # type: ignore[attr-defined]
    execute("CREATE DATABASE IF NOT EXISTS qg_other")
    execute(
        "CREATE TABLE IF NOT EXISTS qg_other.orders (id INT PRIMARY KEY, customer_id INT)"
    )
    executemany(
        "INSERT INTO customers (email, name, country) VALUES (%s, %s, %s)",
        [(f"c{n}@x.test", f"name {n}", "PL" if n % 3 else "DE") for n in range(300)],
    )
    executemany(
        "INSERT INTO orders (customer_id, reference, status, total, created) "
        "VALUES (%s, %s, %s, %s, %s)",
        [
            (
                n % 300 + 1,
                f"ref-{n}",
                ("open", "paid", "sent", "late")[n % 4],
                n % 997,
                f"2024-01-01 00:{n // 60 % 60:02d}:{n % 60:02d}",
            )
            for n in range(ROWS)
        ],
    )
    executemany(
        "INSERT INTO order_items (order_id, product_id, qty) VALUES (%s, %s, 1)",
        [(n % ROWS + 1, n % 40) for n in range(ROWS * 2)],
    )
    executemany(
        "INSERT INTO articles (title, body) VALUES (%s, %s)",
        [(f"title {n}", f"some words about topic{n} and more") for n in range(100)],
    )
    for table in ("tiny", "tiny_plain"):
        executemany(
            f"INSERT INTO {table} (code, label) VALUES (%s, %s)",
            [("a", "first"), ("b", "second"), ("c", "third")],
        )
    for table in TABLES:
        execute(f"ANALYZE TABLE {table}")
        cursor.fetchall()  # type: ignore[attr-defined]


# name -> (statement with %s placeholders, parameters)
CASES: dict[str, tuple[str, tuple[object, ...]]] = {
    "eq_unindexed": ("SELECT id FROM orders WHERE customer_id = %s", (7,)),
    "eq_indexed": ("SELECT id FROM orders WHERE reference = %s", ("ref-7",)),
    "pk_lookup": ("SELECT * FROM orders WHERE id = %s", (7,)),
    "status_low_selectivity": (
        "SELECT id FROM orders WHERE status = %s",
        ("open",),
    ),
    "join_unindexed": (
        (
            "SELECT c.name, o.total FROM customers c "
            "JOIN orders o ON o.customer_id = c.id WHERE c.email = %s"
        ),
        ("c7@x.test",),
    ),
    "join_indexed": (
        (
            "SELECT o.id, i.qty FROM orders o "
            "JOIN order_items i ON i.order_id = o.id WHERE o.reference = %s"
        ),
        ("ref-7",),
    ),
    "join_no_filter": (
        "SELECT o.id, c.name FROM orders o JOIN customers c ON c.id = o.customer_id",
        (),
    ),
    "covering_scan_filter": (
        "SELECT status, created FROM orders WHERE created = %s",
        ("2024-01-01 00:00:07",),
    ),
    "covering_count": ("SELECT COUNT(*) FROM orders", ()),
    "composite_leading_missing": (
        "SELECT * FROM orders WHERE created = %s",
        ("2024-01-01 00:00:07",),
    ),
    "composite_leading_present": (
        "SELECT * FROM orders WHERE status = %s AND created = %s",
        ("open", "2024-01-01 00:00:08"),
    ),
    "like_leading_wildcard": (
        "SELECT id FROM customers WHERE name LIKE %s",
        ("%7",),
    ),
    "in_list": (
        "SELECT id FROM orders WHERE customer_id IN (%s, %s, %s)",
        (1, 2, 3),
    ),
    "or_two_columns": (
        "SELECT id FROM orders WHERE customer_id = %s OR total = %s",
        (7, 8),
    ),
    "subquery_in": (
        (
            "SELECT id FROM customers WHERE id IN "
            "(SELECT customer_id FROM orders WHERE total > %s)"
        ),
        (990,),
    ),
    "order_limit_no_filter": (
        "SELECT * FROM orders ORDER BY total DESC LIMIT 10",
        (),
    ),
    "order_limit_filter": (
        "SELECT * FROM orders WHERE customer_id = %s ORDER BY total LIMIT 10",
        (7,),
    ),
    "update_where": (
        "UPDATE orders SET total = total + 1 WHERE customer_id = %s",
        (7,),
    ),
    "update_pk": ("UPDATE orders SET total = %s WHERE id = %s", (1, 7)),
    "delete_where": ("DELETE FROM orders WHERE total = %s", (5,)),
    "view": ("SELECT id FROM v_open_orders WHERE customer_id = %s", (7,)),
    "fulltext": (
        "SELECT id FROM articles WHERE MATCH(title, body) AGAINST (%s)",
        ("topic7",),
    ),
    "range_unindexed": (
        "SELECT id FROM orders WHERE total BETWEEN %s AND %s",
        (10, 20),
    ),
    "function_of_column": (
        "SELECT id FROM customers WHERE LOWER(name) = %s",
        ("name 7",),
    ),
    "select_all": ("SELECT * FROM orders", ()),
    "type_mismatch": ("SELECT id FROM orders WHERE reference = %s", (7,)),
    "derived_table": (
        "SELECT t.x FROM (SELECT customer_id AS x FROM orders WHERE total = %s) t",
        (5,),
    ),
    "union_same_table": (
        (
            "SELECT id FROM orders WHERE total = %s "
            "UNION SELECT id FROM orders WHERE customer_id = %s"
        ),
        (5, 7),
    ),
    "cte": (
        (
            "WITH o AS (SELECT id, customer_id FROM orders WHERE total = %s) "
            "SELECT * FROM o"
        ),
        (5,),
    ),
    "group_by": (
        "SELECT customer_id, COUNT(*) FROM orders WHERE total > %s GROUP BY customer_id",
        (990,),
    ),
    "two_columns_same_table": (
        "SELECT id FROM orders WHERE total = customer_id",
        (),
    ),
    "not_equal": ("SELECT id FROM orders WHERE customer_id <> %s", (7,)),
    "is_null": ("SELECT id FROM orders WHERE customer_id IS NULL", ()),
    "indexed_but_scanned": (
        "SELECT * FROM orders WHERE status IN (%s, %s, %s, %s)",
        ("open", "paid", "sent", "late"),
    ),
    "tiny_indexed": ("SELECT * FROM tiny WHERE code IN (%s, %s, %s)", ("a", "b", "c")),
    "tiny_unindexed": ("SELECT code FROM tiny_plain WHERE label = %s", ("second",)),
    "index_hint": (
        "SELECT id FROM orders FORCE INDEX (ix_orders_reference) WHERE customer_id = %s",
        (7,),
    ),
    "straight_join": (
        (
            "SELECT o.id FROM customers c STRAIGHT_JOIN orders o "
            "ON o.customer_id = c.id WHERE c.email = %s"
        ),
        ("c7@x.test",),
    ),
    "schema_qualified_other_db": (
        "SELECT id FROM qg_other.orders WHERE customer_id = %s",
        (7,),
    ),
    "self_join": (
        (
            "SELECT a.id FROM orders a JOIN orders b ON b.customer_id = a.customer_id "
            "WHERE a.reference = %s"
        ),
        ("ref-7",),
    ),
}
