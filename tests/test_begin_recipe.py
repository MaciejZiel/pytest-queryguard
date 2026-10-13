"""SQLAlchemy's pysqlite BEGIN recipe in a commit loop (issue #158)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from pytest_queryguard import collector, detectors, listeners
from tests.models import Base, Customer, Order  # noqa: F401  (registers the tables)


@pytest.mark.allow_nplusone
def test_the_begin_of_the_pysqlite_recipe_is_not_a_repeated_statement() -> None:
    listeners.install()
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _connect(dbapi_connection, record):  # type: ignore[no-untyped-def]
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def _begin(conn):  # type: ignore[no-untyped-def]
        conn.exec_driver_sql("BEGIN")

    Base.metadata.create_all(engine)
    rec = collector.QueryCollector()
    collector.push(rec)
    try:
        with Session(engine) as session:
            for i in range(6):
                session.execute(select(Customer).where(Customer.id == i)).all()
                session.commit()
    finally:
        collector.pop(rec)
    begins = [s for s in rec.statements if s.sql.strip().upper() == "BEGIN"]
    assert len(begins) == 6  # recorded, so budgets still count them
    findings = detectors.detect(rec, relationship_threshold=5, repeat_threshold=5)
    repeats = [f for f in findings if isinstance(f, detectors.RepeatFinding)]
    assert [f.count for f in repeats] == [6]  # the SELECT only
    assert all("BEGIN" not in f.sql.upper() for f in repeats)
