from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from pytest_queryguard import QueryBudgetExceeded, max_queries

pytestmark = pytest.mark.allow_nplusone

MODELS = (Path(__file__).parent / "models.py").read_text()

BODY = """
import pytest
from sqlalchemy import create_engine, text


@pytest.mark.max_queries({limit})
def test_budget():
    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        for _ in range(3):
            conn.execute(text("select 1"))
"""


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makepyfile(qgmodels=MODELS)
    return pytester


def test_marker_fails_over_budget(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=BODY.format(limit=2))
    result = project.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*expected at most 2 queries, got 3*"])


def test_marker_passes_at_budget(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=BODY.format(limit=3))
    project.runpytest().assert_outcomes(passed=1)


def test_budget_fails_even_in_off_mode(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=BODY.format(limit=1))
    result = project.runpytest("--queryguard-mode=off")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*expected at most 1 queries, got 3*"])


def test_marker_rejects_invalid_limit(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=BODY.format(limit="'x'"))
    result = project.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*max_queries*test_budget*non-negative integer*"])


def test_budget_failure_comes_before_nplusone(project: pytest.Pytester) -> None:
    project.makepyfile(
        test_a="""
        import pytest
        from qgmodels import Order, make_sync_session


        @pytest.mark.max_queries(1)
        def test_both():
            session = make_sync_session()
            for order in session.query(Order).all():
                _ = order.customer.name
        """
    )
    result = project.runpytest()
    result.assert_outcomes(failed=1)
    out = result.stdout.str()
    assert out.index("expected at most 1 queries") < out.index("N+1 detected")


def test_context_manager_counts_only_inside_block() -> None:
    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        conn.execute(text("select 1"))
        with max_queries(1) as collector:
            conn.execute(text("select 2"))
        conn.execute(text("select 3"))
    assert len(collector.statements) == 1


def test_context_manager_raises_when_exceeded() -> None:
    engine = create_engine("sqlite://")
    with pytest.raises(QueryBudgetExceeded, match="at most 1 queries, got 2"):
        with max_queries(1), engine.connect() as conn:
            conn.execute(text("select 1"))
            conn.execute(text("select 2"))


def test_context_manager_keeps_original_exception() -> None:
    engine = create_engine("sqlite://")
    with pytest.raises(KeyError):
        with max_queries(0), engine.connect() as conn:
            conn.execute(text("select 1"))
            raise KeyError("boom")


def test_context_manager_works_without_pytest() -> None:
    script = textwrap.dedent(
        """
        from sqlalchemy import create_engine, text
        from pytest_queryguard import max_queries

        engine = create_engine("sqlite://")
        with engine.connect() as conn:
            with max_queries(1):
                conn.execute(text("select 1"))
                conn.execute(text("select 2"))
        """
    )
    proc = subprocess.run(
        [sys.executable, "-I", "-c", script], capture_output=True, text=True
    )
    assert proc.returncode == 1
    assert "expected at most 1 queries" in proc.stderr


def test_negative_limit_rejected() -> None:
    with pytest.raises(ValueError, match="limit"):
        max_queries(-1)
