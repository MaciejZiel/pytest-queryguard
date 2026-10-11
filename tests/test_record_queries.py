"""The public API for reading recorded queries: record_queries() and QueryLog."""

from __future__ import annotations

import dataclasses
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

import pytest_queryguard
from pytest_queryguard import (
    CallSite,
    NPlusOneDetected,
    Query,
    QueryLog,
    max_queries,
    record_queries,
)
from tests.models import Order, make_sync_session

# The outer plugin measures these tests too; some cause N+1 on purpose.
pytestmark = pytest.mark.allow_nplusone

MODELS = (Path(__file__).parent / "models.py").read_text()


def test_records_sql_and_call_site_of_each_statement() -> None:
    engine = create_engine("sqlite://")
    with engine.connect() as conn, record_queries() as log:
        conn.execute(text("select 1"))
        conn.execute(text("select 2"))  # second
    assert isinstance(log, QueryLog)
    assert log.count == 2
    first, second = log.queries
    assert (first.sql, second.sql) == ("select 1", "select 2")
    assert first.relationship is None
    assert isinstance(second.callsite, CallSite)
    assert second.callsite.filename == __file__
    assert second.callsite.function == (
        "test_records_sql_and_call_site_of_each_statement"
    )
    source = Path(__file__).read_text().splitlines()
    assert source[second.callsite.lineno - 1].endswith("# second")


def test_lazy_loads_carry_the_relationship() -> None:
    session = make_sync_session()
    with record_queries() as log:
        orders = session.query(Order).order_by(Order.id).all()
        names = [order.customer.name for order in orders]
    assert len(names) == 6
    relationships = [query.relationship for query in log.queries]
    assert relationships == [None, *["Order.customer"] * 3]
    assert "customers" in log.queries[1].sql


def test_count_is_live_inside_the_block_and_fixed_after_it() -> None:
    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        with record_queries() as log:
            assert log.count == 0
            conn.execute(text("select 1"))
            assert log.count == 1
        conn.execute(text("select 2"))
    assert log.count == 1
    assert [query.sql for query in log.queries] == ["select 1"]


def test_queries_from_other_threads_are_recorded() -> None:
    engine = create_engine("sqlite://")

    def worker() -> None:
        with engine.connect() as conn:
            conn.execute(text("select 1"))

    with record_queries() as log:
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
    assert [query.sql for query in log.queries] == ["select 1"]


def test_records_inside_max_queries_and_both_count() -> None:
    engine = create_engine("sqlite://")
    with (
        engine.connect() as conn,
        max_queries(2) as collector,
        record_queries() as log,
    ):
        conn.execute(text("select 1"))
        conn.execute(text("select 2"))
    assert log.count == 2
    assert len(collector.statements) == 2  # the 0.2 behaviour, unchanged


def test_nested_logs_see_only_their_own_block() -> None:
    engine = create_engine("sqlite://")
    with engine.connect() as conn, record_queries() as outer:
        conn.execute(text("select 1"))
        with record_queries() as inner:
            conn.execute(text("select 2"))
    assert (outer.count, inner.count) == (2, 1)


def test_query_and_call_site_are_frozen_values() -> None:
    site = CallSite("/app/orders.py", 9, "list_orders")
    query = Query("select 1", site, None)
    assert query == Query(
        "select 1", CallSite("/app/orders.py", 9, "list_orders"), None
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        query.sql = "select 2"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        site.lineno = 10  # type: ignore[misc]


def test_repr_shows_the_count() -> None:
    with record_queries() as log:
        pass
    assert repr(log) == "<QueryLog 0 queries>"


# --------------------------------------------------------- assert_no_nplusone


def test_assert_no_nplusone_raises_with_the_plugin_message() -> None:
    session = make_sync_session()
    with record_queries() as log:
        for order in session.query(Order).all():
            _ = order.customer.name
    with pytest.raises(NPlusOneDetected) as info:
        log.assert_no_nplusone()
    assert isinstance(info.value, AssertionError)
    message = str(info.value)
    assert message.startswith("queryguard: N+1 detected\n\n")
    assert "  Order.customer was lazy-loaded 3 times\n" in message
    assert "tests/test_record_queries.py:" in message
    assert "select(Order).options(selectinload(Order.customer))" in message


def test_assert_no_nplusone_reports_repeated_statements() -> None:
    engine = create_engine("sqlite://")
    with engine.connect() as conn, record_queries() as log:
        for value in range(5):
            conn.execute(text("select :v"), {"v": value})
    with pytest.raises(NPlusOneDetected, match="the same statement ran 5 times"):
        log.assert_no_nplusone()


def test_assert_no_nplusone_takes_thresholds() -> None:
    session = make_sync_session()
    engine = create_engine("sqlite://")
    with engine.connect() as conn, record_queries() as log:
        for order in session.query(Order).all():
            _ = order.customer.name
        for value in range(5):
            conn.execute(text("select :v"), {"v": value})
    log.assert_no_nplusone(relationship_threshold=4, repeat_threshold=6)
    with pytest.raises(NPlusOneDetected, match="lazy-loaded 3 times"):
        log.assert_no_nplusone(relationship_threshold=3, repeat_threshold=6)


def test_assert_no_nplusone_passes_for_a_clean_block() -> None:
    engine = create_engine("sqlite://")
    with engine.connect() as conn, record_queries() as log:
        for value in range(4):
            conn.execute(text("select :v"), {"v": value})
    log.assert_no_nplusone()


@pytest.mark.parametrize("value", [1, 0, -3, "2", 2.0, True, None])
@pytest.mark.parametrize("name", ["relationship_threshold", "repeat_threshold"])
def test_assert_no_nplusone_rejects_invalid_thresholds(
    name: str, value: object
) -> None:
    with record_queries() as log:
        pass
    with pytest.raises(ValueError, match=f"{name} must be an integer >= 2"):
        log.assert_no_nplusone(**{name: value})  # type: ignore[arg-type]


# ------------------------------------------------------------- in a pytest run


def test_recording_leaves_the_plugin_checks_alone(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(qgmodels=MODELS)
    pytester.makepyfile(
        test_a="""
        import pytest
        from pytest_queryguard import record_queries
        from qgmodels import Order, make_sync_session


        @pytest.fixture
        def session():
            return make_sync_session()


        @pytest.mark.max_queries(2)
        def test_orders(session):
            with record_queries() as log:
                for order in session.query(Order).all():
                    _ = order.customer.name
            assert log.count == 4
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "*queryguard: expected at most 2 queries, got 4",
            "*queryguard: N+1 detected",
        ]
    )


def test_assert_no_nplusone_fails_a_test_with_the_plugin_off(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(qgmodels=MODELS)
    pytester.makepyfile(
        test_a="""
        import pytest
        from pytest_queryguard import record_queries
        from qgmodels import Order, make_sync_session


        @pytest.mark.allow_nplusone
        def test_orders():
            session = make_sync_session()
            with record_queries() as log:
                for order in session.query(Order).all():
                    _ = order.customer.name
            log.assert_no_nplusone()
        """
    )
    result = pytester.runpytest("--queryguard-mode=off")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["E   *NPlusOneDetected: queryguard: N+1 detected*"])


def test_record_queries_works_without_pytest(tmp_path: Path) -> None:
    script = tmp_path / "script.py"
    script.write_text(
        textwrap.dedent(
            """
            from sqlalchemy import create_engine, text
            from pytest_queryguard import record_queries

            engine = create_engine("sqlite://")
            with engine.connect() as conn, record_queries() as log:
                conn.execute(text("select 1"))
            query = log.queries[0]
            print(log.count, query.sql, query.callsite.function)
            """
        )
    )
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "1 select 1 <module>"


# ------------------------------------------------------------- public surface


def test_public_names() -> None:
    assert sorted(pytest_queryguard.__all__) == [
        "CallSite",
        "NPlusOneDetected",
        "Query",
        "QueryBudgetExceeded",
        "QueryGuardInternalWarning",
        "QueryGuardWarning",
        "QueryLog",
        "__version__",
        "max_queries",
        "record_queries",
    ]
    for name in pytest_queryguard.__all__:
        assert hasattr(pytest_queryguard, name)


def test_public_api_type_checks(tmp_path: Path) -> None:
    mypy_api = pytest.importorskip("mypy.api")
    snippet = tmp_path / "use_api.py"
    snippet.write_text(
        textwrap.dedent(
            """
            from pytest_queryguard import CallSite, Query, QueryLog, record_queries

            with record_queries() as log:
                pass
            reveal_type(log)
            reveal_type(log.count)
            reveal_type(log.queries)
            query: Query = log.queries[0]
            reveal_type(query.callsite)
            reveal_type(query.relationship)
            site: CallSite | None = query.callsite
            if site is not None:
                reveal_type(site.lineno)
            log.assert_no_nplusone(relationship_threshold=3)
            log.count = 3
            """
        )
    )
    stdout, _, status = mypy_api.run(
        ["--strict", "--no-incremental", "--no-error-summary", str(snippet)]
    )
    # Older mypy versions print "builtins.int" where newer ones print "int".
    notes = [
        line.split(": ", 1)[1].replace("builtins.", "") for line in stdout.splitlines()
    ]
    assert notes == [
        'note: Revealed type is "pytest_queryguard.api.QueryLog"',
        'note: Revealed type is "int"',
        'note: Revealed type is "tuple[pytest_queryguard.api.Query, ...]"',
        'note: Revealed type is "pytest_queryguard.events.CallSite | None"',
        'note: Revealed type is "str | None"',
        'note: Revealed type is "int"',
        'error: Property "count" defined in "QueryLog" is read-only  [misc]',
    ]
    assert status == 1
