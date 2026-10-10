from __future__ import annotations

from pathlib import Path

import pytest

MODELS = (Path(__file__).parent / "models.py").read_text()

NPLUSONE_TEST = """
from qgmodels import Order, make_sync_session


def test_loop():
    session = make_sync_session()
    for order in session.query(Order).all():
        _ = order.customer.name
"""


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makepyfile(qgmodels=MODELS)
    return pytester


def test_nplusone_fails_test(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=NPLUSONE_TEST)
    result = project.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "*N+1 detected*",
            "*Order.customer was lazy-loaded*",
            "*selectinload(Order.customer)*",
        ]
    )


def test_selectinload_version_passes(project: pytest.Pytester) -> None:
    project.makepyfile(
        test_a="""
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        from qgmodels import Order, make_sync_session


        def test_ok():
            session = make_sync_session()
            stmt = select(Order).options(selectinload(Order.customer))
            for order in session.scalars(stmt):
                _ = order.customer.name
        """
    )
    project.runpytest().assert_outcomes(passed=1)


def test_warn_mode_passes_with_warning(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=NPLUSONE_TEST)
    result = project.runpytest("--queryguard-mode=warn")
    result.assert_outcomes(passed=1, warnings=1)
    result.stdout.fnmatch_lines(["*QueryGuardWarning*N+1 detected*"])


def test_off_mode_ignores(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=NPLUSONE_TEST)
    project.makeini("[pytest]\nqueryguard_mode = off\n")
    project.runpytest().assert_outcomes(passed=1)


def test_cli_overrides_ini(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=NPLUSONE_TEST)
    project.makeini("[pytest]\nqueryguard_mode = off\n")
    project.runpytest("--queryguard-mode=fail").assert_outcomes(failed=1)


def test_allow_nplusone_marker(project: pytest.Pytester) -> None:
    marked = NPLUSONE_TEST.replace(
        "def test_loop", "@pytest.mark.allow_nplusone\ndef test_loop"
    )
    project.makepyfile(test_a="import pytest\n" + marked)
    project.runpytest().assert_outcomes(passed=1)


def test_fixture_queries_not_counted(project: pytest.Pytester) -> None:
    project.makepyfile(
        test_a="""
        import pytest
        from qgmodels import Customer, Order, make_sync_session


        @pytest.fixture
        def session():
            session = make_sync_session()
            for order in session.query(Order).all():
                _ = order.customer.name
            return session


        def test_one_query(session):
            session.get(Customer, 1)
        """
    )
    project.runpytest().assert_outcomes(passed=1)


def test_own_failure_preserved(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=NPLUSONE_TEST + "    assert False\n")
    result = project.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*assert False*"])
    result.stdout.no_fnmatch_line("*N+1 detected*")


def test_invalid_mode_is_usage_error(project: pytest.Pytester) -> None:
    project.makeini("[pytest]\nqueryguard_mode = loud\n")
    result = project.runpytest()
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*queryguard_mode*fail*warn*off*"])


def test_invalid_cli_mode_is_usage_error(project: pytest.Pytester) -> None:
    result = project.runpytest("--queryguard-mode=loud")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*queryguard-mode*fail*warn*off*"])


@pytest.mark.parametrize(
    "option", ["queryguard_relationship_threshold", "queryguard_repeat_threshold"]
)
def test_threshold_below_two_is_usage_error(
    project: pytest.Pytester, option: str
) -> None:
    project.makeini(f"[pytest]\n{option} = 1\n")
    result = project.runpytest()
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines([f"*{option}*2*"])


def test_repeat_threshold_from_ini(project: pytest.Pytester) -> None:
    project.makepyfile(
        test_a="""
        from qgmodels import Customer, make_sync_session


        def test_loop():
            session = make_sync_session()
            for i in (1, 2, 3):
                session.get(Customer, i)
        """
    )
    project.makeini("[pytest]\nqueryguard_repeat_threshold = 3\n")
    project.runpytest().assert_outcomes(failed=1)
