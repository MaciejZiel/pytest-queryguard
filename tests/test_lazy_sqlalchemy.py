"""SQLAlchemy is imported only once the project imports it (backends._sqlalchemy).

Each session runs in a subprocess: in-process pytester sessions make the
plugin import SQLAlchemy up front (see plugin._configure).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.allow_nplusone

MODELS = (Path(__file__).parent / "models.py").read_text()

# the models are imported by the test module only, during collection
NPLUSONE = """
import sys

from qgmodels import Order, make_sync_session


def test_loop():
    session = make_sync_session()
    for order in session.query(Order).all():
        _ = order.customer.name
"""


def _run(pytester: pytest.Pytester, *args: str) -> pytest.RunResult:
    return pytester.runpytest_subprocess(
        "-p", "no:cacheprovider", "-p", "no:randomly", "-p", "no:django", *args
    )


def test_a_project_that_imports_sqlalchemy_while_collecting_is_checked(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(qgmodels=MODELS, test_a=NPLUSONE)
    result = _run(pytester)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*Order.customer was lazy-loaded*"])


def test_strict_mode_set_before_sqlalchemy_is_imported_raises(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(qgmodels=MODELS, test_a=NPLUSONE)
    result = _run(pytester, "-o", "queryguard_strict=true")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*NPlusOneDetected*"])


def test_sqlalchemy_is_not_imported_for_a_project_without_it(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(
        test_a="""
import sys

from pytest_queryguard import record_queries


def test_nothing_imports_sqlalchemy():
    with record_queries():
        pass
    assert "sqlalchemy" not in sys.modules
"""
    )
    _run(pytester, "-o", "queryguard_strict=true").assert_outcomes(passed=1)


def test_sqlalchemy_first_imported_in_the_test_body_is_checked(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(
        qgmodels=MODELS,
        test_a="""
import sys


def test_loop():
    assert "sqlalchemy" not in sys.modules
    from qgmodels import Order, make_sync_session

    session = make_sync_session()
    for order in session.query(Order).all():
        _ = order.customer.name
    assert sys.modules["sqlalchemy"].__spec__.loader.__class__.__name__ != "_Loader"
""",
    )
    result = _run(pytester)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*Order.customer was lazy-loaded*"])


def test_sqlalchemys_files_are_classified_again_once_it_is_imported(
    pytester: pytest.Pytester,
) -> None:
    # classified before the import (as a statement of Django measured earlier
    # would make them), SQLAlchemy's files could be taken for an installed
    # application, and a loop of the test body for the application's
    pytester.makepyfile(
        qgmodels=MODELS,
        test_a="""
import os

from pytest_queryguard import callsite


def test_loop():
    callsite.find_origin()
    before = callsite._prefixes
    import qgmodels  # imports SQLAlchemy; the listeners are installed after it
    import sqlalchemy

    here = os.path.normcase(os.path.dirname(sqlalchemy.__file__)) + os.sep
    assert before is not None and here not in before
    assert callsite._prefixes is None or here in callsite._prefixes
""",
    )
    _run(pytester).assert_outcomes(passed=1)
