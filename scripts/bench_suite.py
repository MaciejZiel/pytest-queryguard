"""Time a whole pytest run of a realistic suite with and without the plugin.

Usage::

    uv run python scripts/bench_suite.py [--tests 300] [--runs 5]
        [--backend sqlite-file|postgres] [--url postgresql+psycopg://...]

A temporary suite of ``--tests`` tests is written to a scratch directory. Each
test runs a few Core statements, a few ORM queries, one insert with a commit
and an eager-loaded relationship read, against a file-based SQLite database
(default) or the PostgreSQL server in ``--url``. The suite runs in a fresh
pytest process with the plugin disabled (``-p no:queryguard``) and enabled
(default settings, ``--queryguard-mode=warn`` so a finding cannot fail the
run), the two interleaved, ``--runs`` times. The time is measured inside
pytest, from session start to session finish, so interpreter start-up and
imports are not counted. ``--index-check`` adds the missing-index check;
``--src`` measures another checkout (to compare two versions).
"""

from __future__ import annotations

import argparse
import os
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

CONFTEST = """
import time
_t = {}
def pytest_sessionstart(session):
    _t["s"] = time.perf_counter()
def pytest_sessionfinish(session):
    with open(__import__("os").environ["QG_BENCH_OUT"], "w") as f:
        f.write(str(time.perf_counter() - _t["s"]))
"""

TESTS = """
import os
import pytest
from sqlalchemy import (Column, ForeignKey, Integer, MetaData, String, Table,
                        create_engine, select, text)
from sqlalchemy.orm import (DeclarativeBase, Mapped, Session, mapped_column,
                            relationship, selectinload)

URL = os.environ["QG_BENCH_URL"]


class Base(DeclarativeBase):
    pass


class Parent(Base):
    __tablename__ = "parents"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), index=True)
    children: Mapped[list["Child"]] = relationship(back_populates="parent")


class Child(Base):
    __tablename__ = "children"
    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("parents.id"), index=True)
    name: Mapped[str] = mapped_column(String(50))
    parent: Mapped[Parent] = relationship(back_populates="children")


@pytest.fixture(scope="session")
def engine():
    engine = create_engine(URL)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        for i in range(1, 51):
            s.add(Parent(id=i, name=f"p{i}", children=[
                Child(id=i * 10 + j, name=f"c{j}") for j in range(3)]))
        s.commit()
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


@pytest.mark.parametrize("n", range(__N__))
def test_case(session, engine, n):
    pid = n % 50 + 1
    with engine.connect() as conn:
        conn.execute(select(Parent.__table__).where(Parent.id == pid)).all()
        conn.execute(text("select count(*) from children")).scalar()
    p = session.get(Parent, pid)
    assert p is not None
    rows = session.scalars(
        select(Parent).where(Parent.id <= pid).options(selectinload(Parent.children))
        .limit(5)).all()
    assert rows
    child = session.scalars(select(Child).where(Child.parent_id == pid)).first()
    assert child.parent.id == pid
    p.name = f"p{pid}"
    session.add(Child(id=100000 + n * 7 + os.getpid() % 5, parent_id=pid, name="new"))
    session.commit()
    session.query(Child).filter(Child.id == 100000 + n * 7 + os.getpid() % 5).delete()
    session.commit()
"""


def run_once(
    tests: int, url: str, enabled: bool, index_check: bool, src: Path
) -> float:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "conftest.py").write_text(CONFTEST)
        (root / "test_suite.py").write_text(TESTS.replace("__N__", str(tests)))
        out = root / "out.txt"
        env = dict(
            os.environ, QG_BENCH_URL=url.replace("{dir}", tmp), QG_BENCH_OUT=str(out)
        )
        env["PYTHONPATH"] = os.pathsep.join(
            p
            for p in (
                str(src),
                env.get("PYTHONPATH"),
            )
            if p
        )
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-x",
            "-p",
            "no:cacheprovider",
            str(root),
            "--rootdir",
            str(root),
        ]
        if enabled:
            cmd += ["--queryguard-mode=warn"]
            cmd += ["--queryguard-index-check"] if index_check else []
        else:
            cmd += ["-p", "no:queryguard"]
        proc = subprocess.run(
            cmd, env=env, capture_output=True, text=True, cwd=tmp, check=False
        )
        if proc.returncode != 0:
            sys.stderr.write(proc.stdout[-3000:] + proc.stderr[-2000:])
            raise SystemExit("suite failed")
        return float(out.read_text())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tests", type=int, default=300)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument(
        "--backend", choices=("sqlite-file", "postgres"), default="sqlite-file"
    )
    ap.add_argument("--url", default=None)
    ap.add_argument("--index-check", action="store_true")
    ap.add_argument(
        "--src",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "src",
        help="the src directory of the plugin checkout to measure",
    )
    args = ap.parse_args()
    url = args.url or "sqlite:///{dir}/bench.db"
    if args.backend == "postgres" and not args.url:
        raise SystemExit("--url is required for postgres")
    off, on = [], []
    for _ in range(args.runs):
        off.append(run_once(args.tests, url, False, False, args.src))
        on.append(run_once(args.tests, url, True, args.index_check, args.src))
    m_off, m_on = statistics.median(off), statistics.median(on)
    n = args.tests
    print(
        f"{args.backend}: {n} tests, median of {args.runs} runs"
        f"{' (index check on)' if args.index_check else ''}"
    )
    print(f"  off {m_off:.3f} s ({m_off / n * 1e3:.2f} ms/test)")
    print(f"  on  {m_on:.3f} s ({m_on / n * 1e3:.2f} ms/test)")
    print(
        f"  overhead {m_on - m_off:.3f} s = {(m_on - m_off) / m_off:.1%}; "
        f"all runs off={[round(x, 2) for x in off]} on={[round(x, 2) for x in on]}"
    )


if __name__ == "__main__":
    main()
