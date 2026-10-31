"""Time the per-statement overhead of the measurement itself.

Usage::

    uv run python scripts/bench_overhead.py [--statements 5000] [--runs 7]

Two loops run against in-memory SQLite: ``--statements`` Core ``SELECT``
statements, and the same number of ORM ``select()`` by primary key. Each loop
runs with measurement off (no listeners, no collector) and on (the listeners
installed and a collector active, as around each test). The script starts a
fresh Python process per configuration, the two interleaved, ``--runs``
times, and prints the median time per statement and the overhead of "on".

With ``--app`` it times the in-application mode instead (``pytest_queryguard.app``):
"off" (nothing imported or watched), "idle" (the engine watched, no request in
progress: what every statement outside a request pays) and "request" (inside a
``track()`` block, as during a request).

``measure_interleaved()`` is the method of the overhead gate
(scripts/bench_gate.py): off and on alternate inside each process.
"""

from __future__ import annotations

import argparse
import gc
import inspect
import json
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    event,
    literal_column,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]


def _database() -> tuple[Engine, Table]:
    """An in-memory SQLite engine with the Core table and 50 ORM rows."""
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
    return engine, table


def _core_loop(engine: Engine, table: Table, statements: int) -> None:
    with engine.connect() as conn:
        for i in range(statements):
            conn.execute(select(table).where(table.c.id == i % 50)).all()


def _orm_loop(engine: Engine, statements: int) -> None:
    with Session(engine) as session:
        for i in range(statements):
            session.execute(select(Item).where(Item.id == i % 50 + 1)).scalars().all()
            session.expunge_all()


def _child(mode: str, statements: int) -> None:
    from pytest_queryguard.collector import QueryCollector, pop, push
    from pytest_queryguard.listeners import install, install_index_check

    engine, table = _database()
    collector = QueryCollector(index_check=mode == "index")
    if mode in ("on", "index"):
        install()
        if mode == "index":
            install_index_check()
        push(collector)
    unit = None
    if mode in ("app-idle", "app-request"):
        from pytest_queryguard import app as queryguard

        queryguard.watch(engine)
        if mode == "app-request":
            # One unit around everything, with no cap and no report: the cost
            # of measuring each statement, not of reporting.
            unit = queryguard.track(max_events=None, report=[])
            unit.__enter__()

    def core() -> None:
        _core_loop(engine, table, statements)

    def orm() -> None:
        _orm_loop(engine, statements)

    def distinct() -> None:
        # A new statement shape each time: every statement is explained.
        with engine.connect() as conn:
            for i in range(statements):
                conn.execute(
                    select(table).where(
                        table.c.id == i % 50, literal_column(f"n != 'v{i}'")
                    )
                ).all()

    results = []
    for loop in (core, orm, distinct):
        loop()  # warm up: statement caches, first-call costs
        start = time.perf_counter()
        loop()
        results.append((time.perf_counter() - start) / statements * 1e6)
    if mode in ("on", "index"):
        pop(collector)
    if unit is not None:
        unit.__exit__(None, None, None)
    print(
        f"{results[0]:.3f} {results[1]:.3f} {len(collector.statements)} "
        f"{results[2]:.3f}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--statements", type=int, default=5000)
    parser.add_argument("--runs", type=int, default=7)
    parser.add_argument(
        "--child",
        choices=("on", "off", "index", "app-idle", "app-request", "interleaved"),
    )
    parser.add_argument("--rounds", type=int, default=20, help=argparse.SUPPRESS)
    parser.add_argument(
        "--index-check",
        action="store_true",
        help="also time the missing-index check (SQLite EXPLAIN QUERY PLAN)",
    )
    parser.add_argument(
        "--app",
        action="store_true",
        help="time the in-application mode (pytest_queryguard.app) instead",
    )
    args = parser.parse_args()
    if args.child == "interleaved":
        _child_interleaved(args.statements, args.rounds)
        return
    if args.child:
        _child(args.child, args.statements)
        return
    if args.app:
        _report_app(args.statements, args.runs)
        return
    if args.index_check:
        _report_index_check(args.statements, args.runs)
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


def _times(
    statements: int, runs: int, modes: tuple[str, ...]
) -> dict[str, list[tuple[float, ...]]]:
    times: dict[str, list[tuple[float, ...]]] = {mode: [] for mode in modes}
    for _ in range(runs):
        for mode in modes:
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
            times[mode].append((float(out[0]), float(out[1]), float(out[3])))
    return times


def _report_index_check(statements: int, runs: int) -> None:
    times = _times(statements, runs, ("off", "on", "index"))
    print(f"{statements} statements per loop, median of {runs} runs (us/statement)")
    print(f"{'loop':<10}{'off':>9}{'on':>9}{'index check':>13}{'index vs on':>13}")
    for index, name in enumerate(("core", "orm", "distinct")):
        off, on, check = (
            statistics.median(t[index] for t in times[mode])
            for mode in ("off", "on", "index")
        )
        print(f"{name:<10}{off:>9.1f}{on:>9.1f}{check:>13.1f}{check - on:>13.1f}")
    print("distinct: every statement is a new shape, so each is explained")


def _report_app(statements: int, runs: int) -> None:
    modes = ("off", "app-idle", "app-request")
    times = _times(statements, runs, modes)
    print(f"{statements} statements per loop, {runs} runs (us/statement)")
    print(
        f"{'loop':<6}{'stat':<8}{'off':>8}{'idle':>8}{'request':>9}"
        f"{'idle+':>8}{'request+':>10}"
    )
    # The minimum is shown next to the median: on a busy machine it is the
    # steadier of the two.
    for index, name in enumerate(("core", "orm")):
        for stat in (statistics.median, min):
            off, idle, request = (stat(t[index] for t in times[mode]) for mode in modes)
            print(
                f"{name:<6}{stat.__name__:<8}{off:>8.1f}{idle:>8.1f}{request:>9.1f}"
                f"{idle - off:>8.1f}{request - off:>10.1f}"
            )
    print("idle: engine watched, no request in progress; request: inside track()")


def measure(statements: int, runs: int) -> dict[str, tuple[float, float]]:
    """Median microseconds per statement, {loop: (off, on)}, for core and orm."""
    times = _times(statements, runs, ("off", "on"))
    return {
        name: (
            statistics.median(t[index] for t in times["off"]),
            statistics.median(t[index] for t in times["on"]),
        )
        for index, name in enumerate(("core", "orm"))
    }


# -- interleaved measurement (the overhead gate) ------------------------------
#
# A shared CI runner is noisy: other virtual machines on the same host, CPU
# frequency changes and the memory layout of each process move a timing by
# more than the overhead being checked. The gate therefore times "off" and
# "on" in the same process, in short blocks that alternate (off, on, on, off,
# ...), so both see the same machine state, and keeps the fastest block of
# each: noise only ever adds time, so the minimum is the estimate least
# disturbed by it. Several processes are used, because a process's memory
# layout is fixed for its lifetime, and the median of their ratios is taken.


def _plugin_listeners() -> list[tuple[type, str, Callable[..., Any]]]:
    """Every (target, event, function) that ``listeners.install()`` registers.

    Discovered rather than listed, so a listener added to ``install()`` later
    is switched on and off with the others.
    """
    from sqlalchemy.pool import Pool

    from pytest_queryguard import listeners

    listeners.install()
    functions = [f for f in vars(listeners).values() if inspect.isfunction(f)]
    found: list[tuple[type, str, Callable[..., Any]]] = [
        (target, name, function)
        for target in (Session, Engine, Pool)
        for name in target.dispatch._event_names
        for function in functions
        if event.contains(target, name, function)
    ]
    if not any(name == "before_cursor_execute" for _, name, _ in found):
        raise RuntimeError("the statement listener was not found")
    return found


def _child_interleaved(statements: int, rounds: int) -> None:
    from pytest_queryguard.collector import QueryCollector, pop, push

    engine, table = _database()
    registered = _plugin_listeners()

    def measurement(on: bool) -> None:
        for target, name, function in registered:
            (event.listen if on else event.remove)(target, name, function)
        if not on:
            # SQLAlchemy sets this class flag when a listener is added and
            # never clears it; left set, every "off" statement would still go
            # through the (empty) event dispatch that a process without the
            # plugin skips.
            Engine._has_events = False

    measurement(False)
    loops = {
        "core": lambda: _core_loop(engine, table, statements),
        "orm": lambda: _orm_loop(engine, statements),
    }
    times: dict[str, dict[str, list[float]]] = {
        name: {"off": [], "on": []} for name in loops
    }

    def block(loop: Callable[[], None], on: bool) -> float:
        # A new collector per block, as around each test.
        collector = QueryCollector()
        if on:
            measurement(True)
            push(collector)
        gc.collect()
        start = time.perf_counter()
        loop()
        elapsed = time.perf_counter() - start
        if on:
            pop(collector)
            measurement(False)
            if len(collector.statements) < statements:
                raise RuntimeError("the collector missed statements")
        return elapsed / statements * 1e6

    for loop in loops.values():  # warm up: statement caches, first-call costs
        block(loop, False)
        block(loop, True)
    for index in range(rounds):
        order = (False, True) if index % 2 == 0 else (True, False)
        for name, loop in loops.items():
            for on in order:
                times[name]["on" if on else "off"].append(block(loop, on))
    print(json.dumps(times))


def measure_interleaved(
    statements: int, rounds: int, processes: int
) -> list[dict[str, tuple[float, float]]]:
    """Per process, {loop: (off, on)}: the fastest block, in us per statement."""
    results = []
    for _ in range(processes):
        child = subprocess.run(
            [
                sys.executable,
                __file__,
                "--child",
                "interleaved",
                "--statements",
                str(statements),
                "--rounds",
                str(rounds),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if child.returncode:
            raise RuntimeError(f"a benchmark process failed:\n{child.stderr}")
        times = json.loads(child.stdout)
        results.append(
            {name: (min(t["off"]), min(t["on"])) for name, t in times.items()}
        )
    return results


if __name__ == "__main__":
    main()
