"""Application N+1s that must stay reported with queryguard_test_code = off.

Each session lays out a project the way real suites do (an installed app, an
app under a folder whose name starts with "test", Django-style ``tests.py``,
fixtures and conftest factories that call the app, step files, test loops
that drive an app helper) and checks that the application's N+1 still fails
with the default configuration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from .conftest import INNER_ADDOPTS

pytestmark = pytest.mark.allow_nplusone

MODELS = (Path(__file__).parent / "models.py").read_text()

APP = """
from sqlalchemy import select
from qgmodels import Customer, Order


def customer_names(session):
    return [o.customer.name for o in session.query(Order).all()]


def lookup(session, name):
    return session.scalars(select(Customer).where(Customer.name == name)).first()


def lookups(session, names):
    return [lookup(session, name) for name in names]
"""

NAMES = "['c1', 'c2', 'c3', 'c4', 'c5']"


@pytest.fixture
def project(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("PYTEST_ADDOPTS", INNER_ADDOPTS)
    pytester.makepyfile(qgmodels=MODELS)
    pytester.makeini("[pytest]\npythonpath = .\n")
    return pytester


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _fails_with_app_finding(result: pytest.RunResult, where: str) -> None:
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines([f"*first at *{where}*"])
    result.stdout.no_fnmatch_line("*in test code left out*")


def test_installed_application_called_from_a_fixture(project: Any) -> None:
    installed = project.path / "venv" / "lib" / "python3" / "site-packages"
    _write(installed, "installedshop/__init__.py", APP)
    project.makeconftest(
        """
        import sys
        sys.path.insert(0, "venv/lib/python3/site-packages")
        import pytest
        from installedshop import customer_names
        from qgmodels import make_sync_session


        @pytest.fixture
        def names():
            return lambda: customer_names(make_sync_session())
        """
    )
    project.makepyfile(
        test_a="""
        def test_app(names):
            assert names()
        """
    )
    result = project.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*Order.customer was lazy-loaded*"])
    result.stdout.no_fnmatch_line("*in test code left out*")


@pytest.mark.parametrize(
    "package", ["test_utils", "apps/tests_service", "src/testing", "src/tests_app"]
)
def test_application_under_a_folder_named_like_tests(
    project: Any, package: str
) -> None:
    _write(project.path, f"{package}/shopapp.py", APP)
    project.makepyfile(
        test_a=f"""
        import sys
        sys.path.insert(0, {package!r})
        from shopapp import customer_names
        from qgmodels import make_sync_session


        def test_app():
            assert customer_names(make_sync_session())
        """
    )
    _fails_with_app_finding(project.runpytest(), "shopapp.py")


def test_django_style_tests_module(project: Any) -> None:
    project.makeini("[pytest]\npythonpath = .\npython_files = tests.py test_*.py\n")
    _write(project.path, "myapp/__init__.py", "")
    _write(project.path, "myapp/services.py", APP)
    _write(
        project.path,
        "myapp/tests.py",
        "from qgmodels import Order, make_sync_session\n"
        "from myapp.services import customer_names\n\n\n"
        "def test_app():\n"
        "    assert customer_names(make_sync_session())\n\n\n"
        "def test_own_loop():\n"
        "    session = make_sync_session()\n"
        "    assert [o.customer.name for o in session.query(Order).all()]\n",
    )
    result = project.runpytest("myapp/tests.py")
    result.assert_outcomes(failed=1, passed=1)
    result.stdout.fnmatch_lines(["*first at myapp/services.py*"])
    result.stdout.fnmatch_lines(["*1 N+1 finding in test code left out (1 test)*"])


def test_fixture_that_calls_the_application(project: Any) -> None:
    project.makepyfile(shop=APP)
    project.makeconftest(
        """
        import pytest
        from qgmodels import make_sync_session
        from shop import customer_names


        @pytest.fixture
        def names():
            session = make_sync_session()

            def run():
                return customer_names(session)

            return run
        """
    )
    project.makepyfile(
        test_a="""
        def test_app(names):
            assert names()
        """
    )
    # the fixture's setup is not measured; the app it calls in the test is
    _fails_with_app_finding(project.runpytest(), "shop.py")


def test_conftest_application_factory(project: Any) -> None:
    project.makepyfile(shop=APP)
    project.makeconftest(
        """
        import pytest
        import shop


        def create_app():
            return {"/names": shop.customer_names, "/lookups": shop.lookups}


        @pytest.fixture
        def app():
            return create_app()
        """
    )
    project.makepyfile(
        test_a=f"""
        from qgmodels import make_sync_session


        def test_names(app):
            assert app["/names"](make_sync_session())


        def test_lookups(app):
            assert app["/lookups"](make_sync_session(), {NAMES}) is not None
        """
    )
    result = project.runpytest()
    result.assert_outcomes(failed=2)
    result.stdout.no_fnmatch_line("*in test code left out*")


def test_step_definitions_calling_the_application(project: Any) -> None:
    # pytest-bdd style: steps in tests/steps.py, run by the test module
    project.makepyfile(shop=APP)
    _write(
        project.path,
        "tests/steps.py",
        "from shop import customer_names, lookups\n\n\n"
        "def when_listing(ctx):\n"
        "    ctx['names'] = customer_names(ctx['session'])\n\n\n"
        f"def when_looking_up(ctx):\n"
        f"    ctx['found'] = lookups(ctx['session'], {NAMES})\n",
    )
    _write(
        project.path,
        "tests/test_feature.py",
        "import sys, os\n"
        "sys.path.insert(0, os.path.dirname(__file__))\n"
        "from steps import when_listing, when_looking_up\n"
        "from qgmodels import make_sync_session\n\n\n"
        "def test_scenario():\n"
        "    ctx = {'session': make_sync_session()}\n"
        "    for step in (when_listing, when_looking_up):\n"
        "        step(ctx)\n",
    )
    result = project.runpytest("tests/test_feature.py")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*Order.customer was lazy-loaded*"])
    result.stdout.fnmatch_lines(["*the same statement ran 5 times*"])


@pytest.mark.parametrize(
    "body",
    [
        # a loop of the test, one app query per pass
        f"    for name in {NAMES}:\n        lookup(session, name)",
        # the loop in a helper under tests/, the query in the application
        f"    helpers.each({NAMES}, lambda n: lookup(session, n))",
        # the app's own loop, called from two lines of the test
        (
            "    lookups(session, ['c1', 'c2', 'c3'])\n"
            "    lookups(session, ['c4', 'c5', 'c6'])"
        ),
        # the app's own loop, called from a comprehension of the test
        "    [lookups(session, [n]) for n in " + NAMES + "]",
    ],
    ids=["test-loop", "helper-loop", "two-lines", "comprehension"],
)
def test_loops_that_drive_an_application_helper(project: Any, body: str) -> None:
    project.makepyfile(shop=APP)
    _write(
        project.path,
        "tests/helpers.py",
        "def each(items, fn):\n    return [fn(item) for item in items]\n",
    )
    _write(
        project.path,
        "tests/test_drive.py",
        "import sys, os\n"
        "sys.path.insert(0, os.path.dirname(__file__))\n"
        "import helpers\n"
        "from qgmodels import make_sync_session\n"
        "from shop import lookup, lookups\n\n\n"
        "def test_drive():\n"
        "    session = make_sync_session()\n" + body + "\n",
    )
    result = project.runpytest("tests/test_drive.py")
    _fails_with_app_finding(result, "shop.py")


def test_a_loop_that_dispatches_on_type(project: Any) -> None:
    project.makepyfile(
        shop=APP
        + """

def _a(session, name):
    return lookup(session, name)


def _b(session, name):
    return lookup(session, name)


def _c(session, name):
    return lookup(session, name)


def _d(session, name):
    return lookup(session, name)


def _e(session, name):
    return lookup(session, name)


RENDER = {"a": _a, "b": _b, "c": _c, "d": _d, "e": _e}


def feed(session, items):
    return [RENDER[kind](session, name) for kind, name in items]
"""
    )
    project.makepyfile(
        test_a="""
        from qgmodels import make_sync_session
        from shop import feed


        def test_feed():
            items = [("a", "c1"), ("b", "c2"), ("c", "c3"), ("d", "c4"), ("e", "c5")]
            feed(make_sync_session(), items)
        """
    )
    _fails_with_app_finding(project.runpytest(), "shop.py")


def test_separate_calls_through_different_helpers_are_no_loop(project: Any) -> None:
    # the other side of the dispatch rule: one function calls two helpers on
    # lines of their own, each runs the lookup: calls on separate lines
    project.makepyfile(
        shop=APP
        + """

def header(session):
    return lookup(session, "c1")


def footer(session):
    return lookup(session, "c1")


def page(session):
    header(session)
    footer(session)
    header(session)
    footer(session)
    header(session)
"""
    )
    project.makepyfile(
        test_a="""
        from qgmodels import make_sync_session
        from shop import page


        def test_page():
            page(make_sync_session())
        """
    )
    project.runpytest().assert_outcomes(passed=1)


def test_a_framework_calling_a_hook_and_a_view_is_no_loop(project: Any) -> None:
    # a framework installed in site-packages calls a request hook and then the
    # view, from one line of the test: they run the lookup on lines of their
    # own (one in the hook, four in the view), which is no loop
    installed = project.path / "venv" / "lib" / "site-packages"
    installed.mkdir(parents=True)
    (installed / "fakeframework.py").write_text(
        "def request(session, hooks, view):\n"
        "    for hook in hooks:\n"
        "        hook(session)\n"
        "    return view(session)\n"
    )
    project.makepyfile(
        shop=APP
        + """

def before_request(session):
    lookup(session, "c1")


def view(session):
    lookup(session, "c1")
    lookup(session, "c2")
    lookup(session, "c3")
    lookup(session, "c4")
"""
    )
    project.makepyfile(
        test_a="""
        import sys
        sys.path.insert(0, "venv/lib/site-packages")
        from fakeframework import request
        from qgmodels import make_sync_session
        from shop import before_request, view


        def test_request():
            request(make_sync_session(), [before_request], view)
        """
    )
    project.runpytest().assert_outcomes(passed=1)
