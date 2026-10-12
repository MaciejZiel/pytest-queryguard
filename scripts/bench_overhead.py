"""Time the per-statement overhead of the measurement itself.

Usage::

    uv run python scripts/bench_overhead.py [--statements 5000] [--runs 7]

Two loops run against in-memory SQLite: ``--statements`` Core ``SELECT``
statements, and the same number of ORM ``select()`` by primary key. Each loop
runs with measurement off (no listeners, no collector) and on (the listeners
installed and a collector active, as around each test). The script starts a
fresh Python process per configuration, the two interleaved, ``--runs``
times, and prints the median time per statement and the overhead of "on".
"""

from __future__ import annotations

import argparse
import statistics
import subprocess
import sys
import time

from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]


def _child(mode: str, statements: int) -> None:
    from pytest_queryguard.collector import QueryCollector, pop, push
    from pytest_queryguard.listeners import install

    metadata = MetaData()
    table = Table(
        "plain", metadata, Column("id", Integer, primary_key=True), Column("n", String)
    )
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata.create_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as seed:
        seed.add_all(Item(id=i, name=f"item {i}") for i in range(1, 51))
        seed.commit()
    collector = QueryCollector()
    if mode == "on":
        install()
        push(collector)

    def core() -> None:
        with engine.connect() as conn:
            for i in range(statements):
                conn.execute(select(table).where(table.c.id == i % 50)).all()

    def orm() -> None:
        with Session(engine) as session:
            for i in range(statements):
                session.execute(
                    select(Item).where(Item.id == i % 50 + 1)
                ).scalars().all()
                session.expunge_all()

    results = []
    for loop in (core, orm):
        loop()  # warm up: statement caches, first-call costs
        start = time.perf_counter()
        loop()
        results.append((time.perf_counter() - start) / statements * 1e6)
    if mode == "on":
        pop(collector)
    print(f"{results[0]:.3f} {results[1]:.3f} {len(collector.statements)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--statements", type=int, default=5000)
    parser.add_argument("--runs", type=int, default=7)
    parser.add_argument("--child", choices=("on", "off"))
    args = parser.parse_args()
    if args.child:
        _child(args.child, args.statements)
        return
    results = measure(args.statements, args.runs)
    print(f"{args.statements} statements per loop, median of {args.runs} runs")
    print(
        f"{'loop':<6}{'off us/stmt':>13}{'on us/stmt':>13}{'overhead us':>13}{'overhead':>10}"
    )
    for name, (off, on) in results.items():
        print(
            f"{name:<6}{off:>13.1f}{on:>13.1f}{on - off:>13.1f}{(on - off) / off:>9.0%}"
        )


def measure(statements: int, runs: int) -> dict[str, tuple[float, float]]:
    """Median microseconds per statement, {loop: (off, on)}, for core and orm."""
    times: dict[str, list[tuple[float, float]]] = {"off": [], "on": []}
    for _ in range(runs):
        for mode in ("off", "on"):
            out = subprocess.run(
                [
                    sys.executable,
                    __file__,
                    "--child",
                    mode,
                    "--statements",
                    str(statements),
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.split()
            times[mode].append((float(out[0]), float(out[1])))
    return {
        name: (
            statistics.median(t[index] for t in times["off"]),
            statistics.median(t[index] for t in times["on"]),
        )
        for index, name in enumerate(("core", "orm"))
    }


if __name__ == "__main__":
    main()
