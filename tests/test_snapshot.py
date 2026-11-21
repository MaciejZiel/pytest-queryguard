"""QueryLog.snapshot(): the recorded statements as short texts for snapshot tests."""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload

from pytest_queryguard import QueryGuardFixture, max_queries, record_queries
from pytest_queryguard.sqltext import fingerprint
from tests.models import Customer, Order, make_sync_session

# The outer plugin measures these tests too; some cause N+1 on purpose.
pytestmark = pytest.mark.allow_nplusone


@pytest.fixture
def session() -> Session:
    return make_sync_session()


def _orders_of(session: Session, name: str) -> list[int]:
    customer = session.scalars(select(Customer).where(Customer.name == name)).one()
    return [o.id for o in customer.orders]


def test_a_snapshot_shows_tables_and_clauses_without_values(session: Session) -> None:
    with record_queries() as log:
        _orders_of(session, "customer 1")
        session.get(Order, 20)
    assert log.snapshot() == [
        "SELECT ... FROM customers WHERE ...",
        "SELECT ... FROM orders WHERE ...",
        "SELECT ... FROM orders WHERE ...",
    ]


def test_other_values_give_the_same_snapshot(session: Session) -> None:
    with record_queries() as first:
        _orders_of(session, "customer 1")
    session.expunge_all()
    with record_queries() as second:
        _orders_of(session, "customer 2")
    assert [q.sql for q in first] == [q.sql for q in second]  # parameters differ
    assert first.snapshot() == second.snapshot()
    with record_queries() as literal:
        session.execute(text("SELECT name FROM customers WHERE id = 3 LIMIT 1"))
        session.execute(text("SELECT name FROM customers WHERE id = 1 LIMIT 5"))
    one, two = literal.snapshot()
    assert one == two == "SELECT ... FROM customers WHERE ... LIMIT ..."


def test_an_n_plus_one_shows_as_repeated_lines(session: Session) -> None:
    with record_queries() as log:
        orders = session.scalars(select(Order).order_by(Order.id)).all()
        for order in orders:
            _ = order.customer.name
    assert log.snapshot() == [
        "SELECT ... FROM orders ORDER BY ...",
        "SELECT ... FROM customers WHERE ...",
        "SELECT ... FROM customers WHERE ...",
        "SELECT ... FROM customers WHERE ...",
    ]
    session.expunge_all()
    with record_queries() as fixed:
        query = select(Order).options(selectinload(Order.customer)).order_by(Order.id)
        for order in session.scalars(query):
            _ = order.customer.name
    assert fixed.snapshot() == [
        "SELECT ... FROM orders ORDER BY ...",
        "SELECT ... FROM customers WHERE ...",
    ]


def test_writes_keep_their_verb_and_table(session: Session) -> None:
    with record_queries() as log:
        customer = Customer(id=9, name="new", orders=[])
        session.add(customer)
        session.flush()
        customer.name = "renamed"
        session.flush()
        session.delete(customer)
        session.flush()
    assert log.snapshot() == [
        "INSERT INTO customers (...) VALUES (...)",
        "UPDATE customers SET ... WHERE ...",
        "DELETE FROM customers WHERE ...",
    ]


def test_transaction_control_is_left_out(session: Session) -> None:
    session.connection()  # the outer transaction is open before the block
    with record_queries() as log:
        with session.begin_nested():
            session.get(Customer, 1)
        session.execute(text("SAVEPOINT mine"))
        session.execute(text("RELEASE SAVEPOINT mine"))
    sql = [q.sql.split()[0].upper() for q in log]
    assert "SAVEPOINT" in sql
    assert "RELEASE" in sql
    assert log.snapshot() == ["SELECT ... FROM customers WHERE ..."]


def test_the_snapshot_of_a_running_block_grows(session: Session) -> None:
    with record_queries() as log:
        assert log.snapshot() == []
        session.get(Customer, 1)
        assert log.snapshot() == ["SELECT ... FROM customers WHERE ..."]
    assert log.snapshot() == ["SELECT ... FROM customers WHERE ..."]


def test_budget_blocks_and_the_fixture_have_snapshots(
    session: Session, queryguard: QueryGuardFixture
) -> None:
    with max_queries(5) as budget:
        session.get(Customer, 1)
    with queryguard.max_queries(5) as local:
        session.get(Customer, 2)
    assert budget.snapshot() == local.snapshot()
    assert queryguard.snapshot() == ["SELECT ... FROM customers WHERE ..."] * 2


def test_the_fixture_snapshot_needs_a_running_test() -> None:
    fixture = QueryGuardFixture()
    with pytest.raises(
        RuntimeError, match=r"queryguard\.snapshot\(\) counts .* has not run"
    ):
        fixture.snapshot()


@pytest.mark.parametrize(
    "sql",
    [
        # The same statement as SQLite, PostgreSQL (two drivers) and MySQL send it.
        'SELECT "t"."id", "t"."name" FROM "t" WHERE "t"."id" = ?',
        "SELECT t.id, t.name FROM t WHERE t.id = %(id_1)s",
        "SELECT t.id, t.name FROM t WHERE t.id = $1",
        "SELECT `t`.`id`, `t`.`name` FROM `t` WHERE `t`.`id` = %s",
        "select t.id from t where t.id = 'x''y' -- note",
        "/* app='x' */ SELECT t.id\n  FROM t\n  WHERE t.id = :id",
    ],
)
def test_the_shape_is_the_same_across_dialects(sql: str) -> None:
    assert fingerprint(sql) == "SELECT ... FROM t WHERE ..."


@pytest.mark.parametrize(
    ("sql", "shape"),
    [
        (
            (
                "SELECT a.id FROM author AS a LEFT OUTER JOIN book AS b ON a.id = b.a_id "
                "WHERE a.name IN (?, ?, ?) ORDER BY a.id"
            ),
            "SELECT ... FROM author LEFT OUTER JOIN book ON ... WHERE ... ORDER BY ...",
        ),
        (
            "SELECT DISTINCT author.name FROM author JOIN book ON author.id = book.a_id",
            "SELECT DISTINCT ... FROM author JOIN book ON ...",
        ),
        # LIMIT, OFFSET and FETCH are one LIMIT, as dialects spell them differently
        ("SELECT t.id FROM t LIMIT ? OFFSET ?", "SELECT ... FROM t LIMIT ..."),
        ("SELECT t.id FROM t LIMIT %s, %s", "SELECT ... FROM t LIMIT ..."),
        (
            "SELECT t.id FROM t OFFSET 5 ROWS FETCH FIRST 3 ROWS ONLY",
            "SELECT ... FROM t LIMIT ...",
        ),
        (
            (
                "SELECT count(*) AS count_1 FROM (SELECT t.id AS t_id FROM t WHERE t.x > ?)"
                " AS anon_1"
            ),
            "SELECT ... FROM (SELECT ... FROM t WHERE ...)",
        ),
        (
            "SELECT a FROM x UNION ALL SELECT b FROM y",
            "SELECT ... FROM x UNION ALL SELECT ... FROM y",
        ),
        (
            "(SELECT a FROM x) UNION (SELECT b FROM y)",
            "(SELECT ... FROM x) UNION (SELECT ... FROM y)",
        ),
        (
            (
                "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) "
                "SELECT i FROM n"
            ),
            (
                "WITH RECURSIVE n(...) AS (SELECT ... UNION ALL SELECT ... FROM n) "
                "SELECT ... FROM n"
            ),
        ),
        (
            "SELECT t.id FROM t WHERE t.a IS DISTINCT FROM t.b",
            "SELECT ... FROM t WHERE ...",
        ),
        (
            "SELECT t.id FROM t WHERE t.id = ? FOR UPDATE SKIP LOCKED",
            "SELECT ... FROM t WHERE ... FOR UPDATE SKIP LOCKED",
        ),
        (
            "SELECT j.value FROM json_each(?) AS j, t, (t1 JOIN t2 ON t1.a = t2.a)",
            "SELECT ... FROM json_each(...), t, (t1 JOIN t2 ON ...)",
        ),
        ("SELECT 1", "SELECT ..."),
        (
            'INSERT INTO "t" ("a", "b") VALUES (%s, %s) RETURNING "t"."id"',
            "INSERT INTO t (...) VALUES (...) RETURNING ...",
        ),
        ("INSERT INTO t (a) VALUES (?), (?), (?)", "INSERT INTO t (...) VALUES (...)"),
        ("INSERT INTO t DEFAULT VALUES", "INSERT INTO t DEFAULT VALUES"),
        (
            "INSERT INTO t (a) VALUES (%s) ON DUPLICATE KEY UPDATE a = VALUES(a)",
            "INSERT INTO t (...) VALUES (...) ON DUPLICATE KEY UPDATE ...",
        ),
        (
            "INSERT INTO t (a) VALUES (?) ON CONFLICT (a) DO UPDATE SET a = excluded.a",
            "INSERT INTO t (...) VALUES (...) ON CONFLICT ... DO UPDATE SET ...",
        ),
        (
            "INSERT OR IGNORE INTO t (a) VALUES (?) ON CONFLICT DO NOTHING",
            "INSERT OR IGNORE INTO t (...) VALUES (...) ON CONFLICT DO NOTHING",
        ),
        ("UPDATE t SET a=?, b=? WHERE t.id = ?", "UPDATE t SET ... WHERE ..."),
        ("DELETE FROM t WHERE t.id = ?", "DELETE FROM t WHERE ..."),
        # names that are not plain keep their quotes; a quoted keyword is a name
        ('SELECT "my t".id FROM "my t"', 'SELECT ... FROM "my t"'),
        ('SELECT x FROM "from" WHERE y', "SELECT ... FROM from WHERE ..."),
        ('SELECT x FROM "t', 'SELECT ... FROM "t'),  # unterminated: kept as is
        ('SELECT x FROM "app".book AS b', "SELECT ... FROM app.book"),
        ("SELECT $tag$a$tag$, $$b$$ FROM t", "SELECT ... FROM t"),
        ("SET application_name = E'a' 'b'", "SET application_name = ..."),
        # review findings: keyword names, standard strings, IS DISTINCT FROM
        ("SELECT t.offset, t.window FROM t", "SELECT ... FROM t"),
        ("SELECT a.values AS limit FROM a", "SELECT ... FROM a"),
        (
            "SELECT a FROM t WHERE b = 'x\\' LIMIT 1",
            "SELECT ... FROM t WHERE ... LIMIT ...",
        ),
        (
            "SELECT a FROM t WHERE b = E'x\\' y' LIMIT 1",
            "SELECT ... FROM t WHERE ... LIMIT ...",
        ),
        ("SELECT a IS DISTINCT FROM b FROM t", "SELECT ... FROM t"),
        ("SELECT a IS NOT DISTINCT FROM b FROM t", "SELECT ... FROM t"),
        ("SELECT a FROM t FETCH FIRST 5 ROWS WITH TIES", "SELECT ... FROM t LIMIT ..."),
        (
            "SELECT a FROM t, LATERAL (SELECT b FROM u) AS s",
            "SELECT ... FROM t, (SELECT ... FROM u)",
        ),
        ("SET x = ARRAY[%s, %s]", "SET x = ARRAY[..., ...]"),
        ("PRAGMA x = 0x10", "PRAGMA x = ..."),
        # other statements are kept, with values as "..."
        ("PRAGMA foreign_keys = ON", "PRAGMA foreign_keys = ON"),
        ("SET search_path TO 'app', $1", "SET search_path TO ..., ..."),
        ("CREATE TABLE x (a INT DEFAULT 'q')", "CREATE TABLE x (...)"),
        ("SELECT 1; SELECT 2;", "SELECT ...; SELECT ..."),
        ("", ""),
    ],
)
def test_shapes(sql: str, shape: str) -> None:
    assert fingerprint(sql) == shape
