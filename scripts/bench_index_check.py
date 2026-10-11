"""Time the missing-index check on a generated suite against PostgreSQL.

Usage::

    QUERYGUARD_PG_URL=postgresql+pg8000://postgres:pg@localhost:5432/postgres \\
        uv run python scripts/bench_index_check.py [--tests 100] [--runs 5]

The suite has ``--tests`` tests, each running the same 10 distinct SELECT
statements. Every statement is served by an index, so the tests pass; the
plans use btree index conditions and casts of varchar columns to text, so
the check reads the catalog for them. The tables are created once per
session and committed, as in a suite with a session-scoped schema. Each
configuration runs ``--runs`` times in a fresh pytest process, the two
interleaved; the script prints the wall time of each run.
"""

from __future__ import annotations

import argparse
import os
import statistics
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

CONFTEST = """
import os

import pytest
from sqlalchemy import (
    Column, DateTime, ForeignKey, Index, Integer, MetaData, String, Table,
    create_engine, text,
)
from sqlalchemy.orm import Session

metadata = MetaData()
accounts = Table(
    "accounts", metadata,
    Column("id", Integer, primary_key=True),
    Column("tenant_id", Integer),
    Column("region", String(20)),
    Column("email", String(100)),
    Column("status", String(20)),
    Column("created_at", DateTime),
    Index("accounts_tenant_status", "tenant_id", "status"),
    Index("accounts_region", "region"),
    Index("accounts_email", "email"),
    Index("accounts_created", "created_at"),
)
invoices = Table(
    "invoices", metadata,
    Column("id", Integer, primary_key=True),
    Column("account_id", ForeignKey("accounts.id")),
    Column("number", String(30)),
    Column("currency", String(3)),
    Column("state", String(20)),
    Index("invoices_account_state", "account_id", "state"),
    Index("invoices_number", "number"),
    Index("invoices_currency", "currency"),
)
SCHEMA = "qg_bench"


@pytest.fixture(scope="session")
def engine():
    url = os.environ["QUERYGUARD_PG_URL"]
    admin = create_engine(url)
    with admin.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        conn.execute(text(f"CREATE SCHEMA {SCHEMA}"))
    engine = create_engine(
        url, connect_args={"startup_params": {"search_path": SCHEMA}}
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO accounts (tenant_id, region, email, status, created_at) "
            "SELECT g % 5, 'r' || (g % 3), 'e' || g, 's' || (g % 4), now() "
            "FROM generate_series(1, 50) g"
        ))
        conn.execute(text(
            "INSERT INTO invoices (account_id, number, currency, state) "
            "SELECT g % 50 + 1, 'n' || g, 'EUR', 'open' "
            "FROM generate_series(1, 100) g"
        ))
    yield engine
    engine.dispose()
    with admin.begin() as conn:
        conn.execute(text(f"DROP SCHEMA {SCHEMA} CASCADE"))
    admin.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as session:
        yield session
        session.rollback()
"""

TESTS = """
import datetime

import pytest
from sqlalchemy import select

from conftest import accounts, invoices

STATEMENTS = [
    select(accounts.c.id).where(accounts.c.tenant_id == 1),
    select(accounts.c.id).where(
        accounts.c.tenant_id == 1, accounts.c.status == "s1"
    ),
    select(accounts.c.id).where(accounts.c.region == "r1"),
    select(accounts.c.id).where(accounts.c.email == "e1"),
    select(accounts.c.id).where(
        accounts.c.created_at > datetime.datetime(2026, 1, 1)
    ),
    select(invoices.c.id).where(invoices.c.account_id == 1),
    select(invoices.c.id).where(
        invoices.c.account_id == 1, invoices.c.state == "open"
    ),
    select(invoices.c.id).where(invoices.c.number == "n1"),
    select(invoices.c.id).where(invoices.c.currency == "EUR"),
    select(invoices.c.id, accounts.c.email)
    .join(accounts, accounts.c.id == invoices.c.account_id)
    .where(accounts.c.email == "e2"),
]


@pytest.mark.parametrize("n", range({tests}))
def test_statements(session, n):
    for stmt in STATEMENTS:
        session.execute(stmt).all()
"""


def _run(project: Path, args: list[str]) -> float:
    start = time.perf_counter()
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )
    elapsed = time.perf_counter() - start
    if result.returncode != 0:
        sys.exit(f"the benchmark suite failed:\n{result.stdout}\n{result.stderr}")
    return elapsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tests", type=int, default=100)
    parser.add_argument("--runs", type=int, default=5)
    options = parser.parse_args()
    if not os.environ.get("QUERYGUARD_PG_URL"):
        sys.exit("set QUERYGUARD_PG_URL to a PostgreSQL 16+ server")
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        (project / "conftest.py").write_text(textwrap.dedent(CONFTEST))
        (project / "test_bench.py").write_text(TESTS.format(tests=options.tests))
        configs = {
            "check off": ["--no-queryguard-index-check"],
            "check on": ["--queryguard-index-check"],
        }
        _run(project, configs["check on"])  # warm up the server and imports
        times: dict[str, list[float]] = {name: [] for name in configs}
        for _ in range(options.runs):  # interleaved, so drift hits both alike
            for name, args in configs.items():
                times[name].append(_run(project, args))
        for name, runs in times.items():
            shown = ", ".join(f"{t:.2f}" for t in runs)
            print(
                f"{name:>9}: median {statistics.median(runs):.2f} s, "
                f"min {min(runs):.2f} s ({shown})"
            )


if __name__ == "__main__":
    main()
