from __future__ import annotations

from pathlib import Path

import pytest

from pytest_queryguard import plugin

# pytester runs sessions in-process, so the outer plugin sees their queries too.
pytestmark = pytest.mark.allow_nplusone

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


@pytest.mark.skipif(
    pytest.version_tuple < (9,), reason="native TOML config needs pytest 9"
)
def test_non_string_mode_from_native_toml_is_usage_error(
    project: pytest.Pytester,
) -> None:
    project.path.joinpath("pytest.toml").write_text("[pytest]\nqueryguard_mode = 1\n")
    result = project.runpytest()
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*queryguard_mode*fail*warn*off*"])
    result.stderr.no_fnmatch_line("*INTERNALERROR*")


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


@pytest.mark.skipif(
    pytest.version_tuple < (9,), reason="native TOML config needs pytest 9"
)
def test_threshold_from_native_toml_int(project: pytest.Pytester) -> None:
    project.makepyfile(
        test_a="""
        from qgmodels import Customer, make_sync_session


        def test_loop():
            session = make_sync_session()
            for i in (1, 2, 3):
                session.get(Customer, i)
        """
    )
    project.path.joinpath("pytest.toml").write_text(
        "[pytest]\nqueryguard_repeat_threshold = 3\n"
    )
    project.runpytest().assert_outcomes(failed=1)


def test_repeat_threshold_from_pyproject_ini_options(
    project: pytest.Pytester,
) -> None:
    project.makepyfile(
        test_a="""
        from qgmodels import Customer, make_sync_session


        def test_loop():
            session = make_sync_session()
            for i in (1, 2, 3):
                session.get(Customer, i)
        """
    )
    project.makepyprojecttoml(
        '[tool.pytest.ini_options]\nqueryguard_repeat_threshold = "3"\n'
    )
    project.runpytest().assert_outcomes(failed=1)


def test_threshold_from_pyproject_ini_options(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=NPLUSONE_TEST)
    project.makepyprojecttoml(
        '[tool.pytest.ini_options]\nqueryguard_relationship_threshold = "4"\n'
    )
    project.runpytest().assert_outcomes(passed=1)


def test_non_integer_threshold_is_usage_error(project: pytest.Pytester) -> None:
    project.makeini("[pytest]\nqueryguard_repeat_threshold = many\n")
    result = project.runpytest()
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*queryguard_repeat_threshold*"])


BROKEN_CALLSITE = """
import pytest
from pytest_queryguard import listeners


@pytest.fixture(autouse=True)
def _broken(monkeypatch):
    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(listeners, "find_callsite", boom)
"""


def test_internal_error_warned_once_even_if_test_fails(
    project: pytest.Pytester,
) -> None:
    project.makeconftest(BROKEN_CALLSITE)
    project.makepyfile(
        test_a="""
        from qgmodels import Customer, make_sync_session


        def test_first():
            make_sync_session().get(Customer, 1)
            assert False


        def test_second():
            make_sync_session().get(Customer, 1)
        """
    )
    result = project.runpytest()
    result.assert_outcomes(passed=1, failed=1, warnings=1)
    result.stdout.fnmatch_lines(["*QueryGuardInternalWarning*boom*"])


def test_detection_error_does_not_fail_test(
    project: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise ValueError("detect broke")

    monkeypatch.setattr(plugin, "detect", boom)
    project.makepyfile(test_a=NPLUSONE_TEST)
    result = project.runpytest()
    result.assert_outcomes(passed=1, warnings=1)
    result.stdout.fnmatch_lines(["*QueryGuardInternalWarning*detect broke*"])


def test_inner_internal_error_does_not_leak_into_outer_session(
    project: pytest.Pytester,
) -> None:
    from pytest_queryguard import listeners

    project.makeconftest(BROKEN_CALLSITE)
    project.makepyfile(test_a=NPLUSONE_TEST)
    project.runpytest()
    assert listeners.internal_error() is None


def test_warn_mode_points_at_the_test(project: pytest.Pytester) -> None:
    project.makepyfile(test_a=NPLUSONE_TEST)
    result = project.runpytest("--queryguard-mode=warn")
    result.stdout.fnmatch_lines(["*test_a.py:*: QueryGuardWarning*"])
    result.stdout.no_fnmatch_line("*plugin.py*")


def test_outer_error_survives_nested_session(
    project: pytest.Pytester,
) -> None:
    from pytest_queryguard import listeners

    outer = RuntimeError("outer")
    listeners.record_error(outer)
    project.makepyfile(test_a=NPLUSONE_TEST)
    project.runpytest().assert_outcomes(failed=1)  # the inner session is unaffected
    assert listeners.internal_error() is outer
    listeners.swap_error(None)
