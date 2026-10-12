"""Time the missing-index check on a live MySQL or MariaDB server.

    QUERYGUARD_MYSQL_URL=mysql+pymysql://root:qg@127.0.0.1:3306/mysql \
        uv run python scripts/bench_mysql_index.py --statements 1000 --runs 5

Prints the median microseconds per statement with measurement off, with the
listeners on, and with the index check on, for a repeated statement shape
(one EXPLAIN per test) and for a new shape every time (one EXPLAIN each).
Server round trips dominate, so the numbers depend on the server and network.
"""

from __future__ import annotations

import argparse
import os
import statistics
import time
import uuid
from collections.abc import Callable

import pymysql
from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    literal_column,
    select,
)
from sqlalchemy.engine import make_url


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--statements", type=int, default=1000)
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    url = make_url(os.environ["QUERYGUARD_MYSQL_URL"])
    admin = pymysql.connect(
        host=url.host, port=url.port or 3306, user=url.username, password=url.password,
        autocommit=True,
    )  # fmt: skip
    database = f"qg_bench_{uuid.uuid4().hex[:8]}"
    cursor = admin.cursor()
    cursor.execute(f"CREATE DATABASE {database}")
    try:
        engine = create_engine(url.set(database=database))
        metadata = MetaData()
        table = Table(
            "plain",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("n", String(20)),
        )
        metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(
                table.insert(), [{"id": i, "n": f"n{i}"} for i in range(1, 51)]
            )
        from pytest_queryguard.collector import QueryCollector, pop, push
        from pytest_queryguard.listeners import install, install_index_check

        def same() -> None:
            with engine.connect() as conn:
                for i in range(args.statements):
                    conn.execute(select(table).where(table.c.id == i % 50)).all()

        def distinct() -> None:
            with engine.connect() as conn:
                for i in range(args.statements):
                    conn.execute(
                        select(table).where(
                            table.c.id == i % 50, literal_column(f"n != 'v{i}'")
                        )
                    ).all()

        def measure(loop: Callable[[], None], index: bool = False) -> float:
            loop()  # warm up: statement caches, first-call costs
            if index:  # a fresh test: nothing is explained yet
                collector = QueryCollector(index_check=True)
                push(collector)
            start = time.perf_counter()
            loop()
            took = (time.perf_counter() - start) / args.statements * 1e6
            if index:
                pop(collector)
            return took

        rows: dict[str, dict[str, list[float]]] = {
            name: {"off": [], "on": [], "index": []} for name in ("same", "distinct")
        }
        for _ in range(args.runs):
            for name, loop in (("same", same), ("distinct", distinct)):
                rows[name]["off"].append(measure(loop))
        install()
        collector = QueryCollector()
        push(collector)
        for _ in range(args.runs):
            for name, loop in (("same", same), ("distinct", distinct)):
                rows[name]["on"].append(measure(loop))
        pop(collector)
        install_index_check()
        for _ in range(args.runs):
            for name, loop in (("same", same), ("distinct", distinct)):
                rows[name]["index"].append(measure(loop, index=True))
        print(f"{'loop':<10}{'off':>9}{'on':>9}{'index check':>13}{'index vs on':>13}")
        for name, values in rows.items():
            off, on, index = (
                statistics.median(values[k]) for k in ("off", "on", "index")
            )
            print(f"{name:<10}{off:>9.1f}{on:>9.1f}{index:>13.1f}{index - on:>+13.1f}")
        engine.dispose()
    finally:
        cursor.execute(f"DROP DATABASE IF EXISTS {database}")
        admin.close()


if __name__ == "__main__":
    main()
