"""Time what the experimental unused eager-load check costs.

Usage::

    uv run python scripts/bench_eager_overhead.py [--reads 200000] [--runs 7]

Three configurations, each in a fresh Python process, interleaved ``--runs``
times (median reported):

* ``off``: the check is not enabled, nothing is installed (the default);
* ``idle``: ``eager.enable()`` was called but no test body is being tracked
  (fixtures, setup, teardown, and every test while the check is on);
* ``on``: enabled and tracking, as around the body of each test.

Two loops: attribute reads of loaded instances (a column and a relationship),
in nanoseconds per read, and ORM ``SELECT`` statements with a ``selectinload()``
option, in microseconds per statement.
"""

from __future__ import annotations

import argparse
import statistics
import subprocess
import sys
import time

from sqlalchemy import ForeignKey, create_engine, select
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    selectinload,
)
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


class Parent(Base):
    __tablename__ = "parent"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    kids: Mapped[list[Kid]] = relationship()


class Kid(Base):
    __tablename__ = "kid"

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("parent.id"))


def _child(mode: str, reads: int) -> None:
    from pytest_queryguard import eager

    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as seed:
        seed.add_all(
            Parent(id=i, name=f"p{i}", kids=[Kid(id=i * 10), Kid(id=i * 10 + 1)])
            for i in range(1, 51)
        )
        seed.commit()
    tracker = None
    if mode != "off":
        eager.enable()
    if mode == "on":
        tracker = eager.start()
    session = Session(engine)
    rows = session.scalars(select(Parent).options(selectinload(Parent.kids))).all()

    def attributes() -> None:
        for i in range(reads):
            row = rows[i % 50]
            row.name  # noqa: B018
            row.kids  # noqa: B018

    def selects() -> None:
        for i in range(2000):
            session.execute(
                select(Parent)
                .where(Parent.id == i % 50 + 1)
                .options(selectinload(Parent.kids))
            ).scalars().all()
            session.expunge_all()

    out = []
    attributes()
    start = time.perf_counter()
    attributes()
    out.append((time.perf_counter() - start) / (reads * 2) * 1e9)
    selects()
    start = time.perf_counter()
    selects()
    out.append((time.perf_counter() - start) / 2000 * 1e6)
    if tracker is not None:
        eager.stop(tracker)
    print(f"{out[0]:.1f} {out[1]:.1f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reads", type=int, default=200_000)
    parser.add_argument("--runs", type=int, default=7)
    parser.add_argument("--child", choices=("off", "idle", "on"))
    args = parser.parse_args()
    if args.child:
        _child(args.child, args.reads)
        return
    times: dict[str, list[tuple[float, float]]] = {"off": [], "idle": [], "on": []}
    for _ in range(args.runs):
        for mode, values in times.items():
            out = subprocess.run(
                [sys.executable, __file__, "--child", mode, "--reads", str(args.reads)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.split()
            values.append((float(out[0]), float(out[1])))
    print(f"median of {args.runs} runs")
    print(f"{'mode':<6}{'ns/attribute read':>20}{'us/select+selectinload':>26}")
    for mode, values in times.items():
        read = statistics.median(v[0] for v in values)
        select_ = statistics.median(v[1] for v in values)
        print(f"{mode:<6}{read:>20.1f}{select_:>26.1f}")


if __name__ == "__main__":
    main()
