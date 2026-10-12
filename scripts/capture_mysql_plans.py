"""Capture real EXPLAIN output from MySQL and MariaDB for the plan fixtures.

    QUERYGUARD_MYSQL_URL=mysql+pymysql://root:qg@127.0.0.1:33806/qg \
        uv run python scripts/capture_mysql_plans.py

Writes tests/fixtures/plans/mysql<major>/<name>.json or mariadb<major>/<name>.json
for the server the URL points at: the tabular EXPLAIN the check reads, the
EXPLAIN FORMAT=JSON of the same statement for reference, and the catalog facts
(base table, columns, leading index columns) the analysis asks for. The
statements run in a scratch database that is dropped again.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pymysql
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests import mysql_cases

from pytest_queryguard import mysql_index


def _rows(cursor: Any) -> list[dict[str, Any]]:
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def main() -> None:
    url = make_url(os.environ["QUERYGUARD_MYSQL_URL"])
    connection = pymysql.connect(
        host=url.host or "127.0.0.1",
        port=url.port or 3306,
        user=url.username or "root",
        password=url.password or "",
        autocommit=True,
    )
    cursor = connection.cursor()
    cursor.execute("SELECT VERSION()")
    version = str(cursor.fetchone()[0])  # type: ignore[index]
    maria = "mariadb" in version.lower()
    major = version.split(".")[0]
    name = ("mariadb" if maria else "mysql") + major
    scratch = "qg_capture"
    cursor.execute(f"DROP DATABASE IF EXISTS {scratch}")
    cursor.execute("DROP DATABASE IF EXISTS qg_other")
    cursor.execute(f"CREATE DATABASE {scratch}")
    cursor.execute(f"USE {scratch}")
    try:
        for statement in mysql_cases.DDL:
            cursor.execute(statement)
        mysql_cases.fill(cursor)
        schema = mysql_index.Schema(cursor)
        facts: dict[str, Any] = {}
        for table in (*mysql_cases.TABLES, "v_open_orders"):  # the view: no base table
            base = schema.table(table)
            facts[table] = {
                "table": base,
                "columns": schema.columns(base) if base else [],
                "leading": sorted(schema.leading(base)) if base else [],
            }
        out = ROOT / "tests" / "fixtures" / "plans" / name
        out.mkdir(parents=True, exist_ok=True)
        for case, (sql, params) in mysql_cases.CASES.items():
            cursor.execute("EXPLAIN " + sql, params)
            tabular = _rows(cursor)
            cursor.execute("EXPLAIN FORMAT=JSON " + sql, params)
            raw = cursor.fetchone()[0]  # type: ignore[index]
            document = {
                "server": version,
                "sql": sql,
                "params": list(params),
                "explain": tabular,
                "explain_json": json.loads(raw),
                "catalog": facts,
            }
            (out / f"{case}.json").write_text(
                json.dumps(document, indent=2, default=str) + "\n", encoding="utf-8"
            )
        print(f"{version}: {len(mysql_cases.CASES)} plans in {out}")
    finally:
        cursor.execute(f"DROP DATABASE IF EXISTS {scratch}")
        cursor.execute("DROP DATABASE IF EXISTS qg_other")
        connection.close()


if __name__ == "__main__":
    main()
