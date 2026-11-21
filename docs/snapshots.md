# Snapshots of the statements

A query budget says how many statements a block may run. A snapshot says
which ones: `log.snapshot()` gives the statements of a `QueryLog` as a list
of short texts, one per statement, with the values left out:

```python
from pytest_queryguard import record_queries


def test_book_page(client):
    with record_queries() as log:
        client.get("/books/1/")
    assert log.snapshot() == [
        "SELECT ... FROM library_book WHERE ... LIMIT ...",
        "SELECT ... FROM library_author WHERE ... LIMIT ...",
    ]
```

The list is a plain `list[str]`, so it compares with a list written by hand,
and with [inline-snapshot](https://github.com/15r10nk/inline-snapshot),
which writes the list into the test for you and updates it when the
statements change:

```python
from inline_snapshot import snapshot

from pytest_queryguard import record_queries


def test_book_page(client):
    with record_queries() as log:
        client.get("/books/1/")
    assert log.snapshot() == snapshot()
```

`pytest --inline-snapshot=create` fills in the empty `snapshot()`, and
`--inline-snapshot=fix` rewrites it after a change you meant to make; in a
normal run a different list fails the test with a diff of the two lists.
inline-snapshot is not a dependency of pytest-queryguard: install it in your
project's test dependencies if you want it. This is what
[inline-snapshot-django](https://github.com/adamchainz/inline-snapshot-django)
does for Django, here for SQLAlchemy and Django alike.

It works with SQLAlchemy and Django, on every log: `record_queries()`,
`max_queries()`, `max_db_time()` and the `queryguard` fixture's
`max_queries()` and `num_queries()` blocks. `queryguard.snapshot()` is the
snapshot of the whole test function (`queryguard.queries`), from every
thread:

```python
def test_book_page_in_full(queryguard, client):
    client.get("/books/1/")
    assert queryguard.snapshot() == [
        "SELECT ... FROM library_book WHERE ... LIMIT ...",
        "SELECT ... FROM library_author WHERE ... LIMIT ...",
    ]
```

## What a line shows

Each statement keeps its clause keywords and the names of its tables; the
text of every other clause becomes `...`:

| Statement | Line |
|---|---|
| `SELECT book.id, book.title FROM book WHERE book.id = ?` | `SELECT ... FROM book WHERE ...` |
| `SELECT a.id FROM author AS a LEFT OUTER JOIN book AS b ON a.id = b.author_id ORDER BY a.id` | `SELECT ... FROM author LEFT OUTER JOIN book ON ... ORDER BY ...` |
| `SELECT count(*) FROM (SELECT book.id FROM book WHERE book.pages > $1) AS anon_1` | `SELECT ... FROM (SELECT ... FROM book WHERE ...)` |
| `INSERT INTO "book" ("title", "pages") VALUES (%s, %s) RETURNING "book"."id"` | `INSERT INTO book (...) VALUES (...) RETURNING ...` |
| `UPDATE book SET title=?, pages=? WHERE book.id = ?` | `UPDATE book SET ... WHERE ...` |
| `DELETE FROM book WHERE book.id = %(id_1)s` | `DELETE FROM book WHERE ...` |
| `SELECT book.id FROM book LIMIT ? OFFSET ?` | `SELECT ... FROM book LIMIT ...` |
| `SELECT book.id FROM book WHERE book.id = ? FOR UPDATE SKIP LOCKED` | `SELECT ... FROM book WHERE ... FOR UPDATE SKIP LOCKED` |

In more detail:

- Column lists, conditions, values, `ON` and `SET` clauses become `...`;
  `SELECT DISTINCT` keeps its `DISTINCT`. The column list of an `INSERT`, a
  `VALUES` list of any number of rows, and the arguments of a table function
  are `(...)`.
- Table aliases are dropped. A subquery in `FROM`, around a `UNION`, or in
  a `WITH` is shaped the same way, inside its parentheses; a subquery in a
  `WHERE` is part of the `...`.
- `LIMIT`, `OFFSET` and `FETCH` are one `LIMIT ...`, because SQLite,
  PostgreSQL and MySQL write paging differently.
- Comments (sqlcommenter tags included) are dropped, whitespace is one
  space, keywords are upper case, and a quoted name made of letters, digits
  and underscores loses its quotes: `"book"`, `` `book` `` and `book` are
  the same. Parameters of every driver style (`?`, `%s`, `%(name)s`, `$1`,
  `:name`) and literals are values. So the same ORM query gives the same
  line on SQLite, PostgreSQL and MySQL.
- Statements other than `SELECT`, `INSERT`, `UPDATE`, `DELETE`,
  `REPLACE INTO`, `VALUES` and `WITH` (a `PRAGMA`, a `SET`, DDL) are kept as written, with each
  literal or parameter as `...`.
- Statements that only open, end or mark a transaction (`BEGIN`, `COMMIT`,
  `ROLLBACK`, `SAVEPOINT`, `RELEASE SAVEPOINT`, `START TRANSACTION`) are
  left out, so a `transaction.atomic()` or `session.begin_nested()` block
  does not change the snapshot; `log.count` and the budgets still count
  them. The plugin's own statements (the index check's `EXPLAIN`) are not
  in any `QueryLog`, so they are not in a snapshot either.

Two statements with the same line can still differ in their values or
columns: a snapshot pins which statements run, in which order and on which
tables, not the exact SQL. Use `log.queries` to assert on the SQL text
itself.

## Limits

- The line is the ORM's statement, shaped: when the ORM writes a statement
  differently on another database (SQLAlchemy's bulk `INSERT` with
  `RETURNING` on PostgreSQL, for one), the line differs too.
- The shaping reads the text with a small tokenizer, not a SQL parser: a
  statement it does not know is shaped less well (a keyword it does not
  know is part of the `...`), but always the same way.
- A string literal written into the SQL ends at its closing quote, as in
  standard SQL; a backslash escapes only in PostgreSQL's `E'...'`. A MySQL
  literal with a backslash-escaped quote (`'it\'s'`) written into the SQL
  text, not passed as a parameter, can be shaped wrongly.
- With several Django databases the lines do not say which database ran
  the statement.
