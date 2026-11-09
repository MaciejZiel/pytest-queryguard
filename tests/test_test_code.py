"""Findings of the test code (queryguard_test_code) and calls on separate lines.

These sessions run with the default ``queryguard_test_code = off``: the
autouse fixture of tests/conftest.py gives the other inner sessions ``fail``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from pytest_queryguard import callsite
from pytest_queryguard.messages import format_test_code_summary

from .conftest import INNER_ADDOPTS

# pytester runs sessions in-process, so the outer plugin sees their queries too.
pytestmark = pytest.mark.allow_nplusone

MODELS = (Path(__file__).parent / "models.py").read_text()

# The application: a loop, a lookup, a handler with a lazy load.
APP = """
from sqlalchemy import select
from qgmodels import Customer, Order


def customer_names(session):
    return [o.customer.name for o in session.query(Order).all()]


def lookup(session, name):
    return session.scalars(select(Customer).where(Customer.name == name)).first()


def lookups(session, names):
    return [lookup(session, name) for name in names]


def order_page(session, order_id):
    lookup(session, "c0")
    return session.get(Order, order_id).customer.name
"""

TEST_LOOP = """
from qgmodels import Order, make_sync_session


def test_loop():
    session = make_sync_session()
    for order in session.query(Order).all():
        _ = order.customer.name
"""

SUMMARY = [
    "*= queryguard: test code =*",
    (
        "1 N+1 finding in test code left out (1 test): the statements ran only in "
        "test modules, conftest.py, factories or helpers under tests/, not in the "
        "application."
    ),
    (
        "To see it: pytest -o queryguard_test_code=warn, or set queryguard_test_code "
        "= warn (or fail) in the pytest configuration."
    ),
    "Why: https://maciejziel.github.io/pytest-queryguard/how-detection-works.html#test-code",
]


@pytest.fixture
def project(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("PYTEST_ADDOPTS", INNER_ADDOPTS)
    pytester.makepyfile(qgmodels=MODELS, shop=APP)
    pytester.makeini("[pytest]\n")
    return pytester


def test_a_loop_in_the_test_function_passes_and_is_counted(project: Any) -> None:
    project.makepyfile(test_a=TEST_LOOP)
    result = project.runpytest()
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines([*SUMMARY, "  test_a.py::test_loop"])
    result.stdout.no_fnmatch_line("*N+1 detected*")


def test_warn_reports_it_as_a_warning(project: Any) -> None:
    project.makepyfile(test_a=TEST_LOOP)
    result = project.runpytest("-o", "queryguard_test_code=warn")
    result.assert_outcomes(passed=1, warnings=1)
    result.stdout.fnmatch_lines(
        ["*QueryGuardWarning: queryguard: N+1 detected", "*Order.customer was lazy-*"]
    )
    result.stdout.no_fnmatch_line("*in test code left out*")


def test_fail_reports_it_as_before(project: Any) -> None:
    project.makepyfile(test_a=TEST_LOOP)
    result = project.runpytest("-o", "queryguard_test_code=FAIL")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*Order.customer was lazy-loaded*"])


def test_an_invalid_value_is_a_usage_error(project: Any) -> None:
    project.makepyfile(test_a=TEST_LOOP)
    result = project.runpytest("-o", "queryguard_test_code=maybe")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*queryguard_test_code*"])


def test_an_application_loop_called_from_the_test_fails(project: Any) -> None:
    project.makepyfile(
        test_a="""
        from qgmodels import make_sync_session
        from shop import customer_names


        def test_app():
            assert customer_names(make_sync_session())
        """
    )
    result = project.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        ["*Order.customer was lazy-loaded*", "*first at shop.py:*in customer_names"]
    )
    result.stdout.no_fnmatch_line("*in test code left out*")


def test_a_helper_under_tests_is_test_code(project: Any) -> None:
    project.mkdir("tests")
    (project.path / "tests" / "helpers.py").write_text(
        "def names(session):\n"
        "    from qgmodels import Order\n"
        "    return [o.customer.name for o in session.query(Order).all()]\n"
    )
    project.makepyfile(
        test_a="""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tests"))
        from helpers import names
        from qgmodels import make_sync_session


        def test_helper():
            assert names(make_sync_session())
        """
    )
    result = project.runpytest()
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(SUMMARY)


@pytest.mark.parametrize("layout", ["ini in tests", "run in tests"])
def test_a_helper_is_test_code_when_the_rootdir_is_the_tests_folder(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, layout: str
) -> None:
    # tests/ itself is the rootdir: its pytest.ini is the configuration, or
    # with no configuration file pytest runs from inside it
    monkeypatch.setenv("PYTEST_ADDOPTS", INNER_ADDOPTS)
    pytester.makepyfile(qgmodels=MODELS, shop=APP)
    pytester.syspathinsert()
    (pytester.path / ".git").mkdir()  # the project's root
    (pytester.path / "tests" / "utils").mkdir(parents=True)
    (pytester.path / "tests" / "utils" / "client.py").write_text(
        "def names(session):\n"
        "    from qgmodels import Order\n"
        "    return [o.customer.name for o in session.query(Order).all()]\n"
    )
    (pytester.path / "tests" / "test_a.py").write_text(
        "from qgmodels import make_sync_session\n"
        "from utils.client import names\n"
        "\n"
        "\n"
        "def test_helper():\n"
        "    assert names(make_sync_session())\n"
    )
    if layout == "ini in tests":
        (pytester.path / "tests" / "pytest.ini").write_text("[pytest]\n")
        result = pytester.runpytest("tests")
    else:
        monkeypatch.chdir(pytester.path / "tests")
        result = pytester.runpytest()
    result.stdout.fnmatch_lines([f"rootdir: {pytester.path / 'tests'}*"])
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(SUMMARY)


def test_a_conftest_client_calling_the_application_is_the_application(
    project: Any,
) -> None:
    project.makeconftest(
        """
        import pytest
        from shop import customer_names


        class Client:
            def get(self, session):
                return customer_names(session)


        @pytest.fixture
        def client():
            return Client()
        """
    )
    project.makepyfile(
        test_a="""
        from qgmodels import make_sync_session


        def test_client(client):
            assert client.get(make_sync_session())
        """
    )
    result = project.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        ["*first at shop.py:*in customer_names", "*called from conftest.py:*in get"]
    )


def test_a_factory_module_is_test_code(project: Any) -> None:
    pytest.importorskip("factory")
    project.makepyfile(
        seeding="""
        import factory
        from factory.alchemy import SQLAlchemyModelFactory
        from sqlalchemy import select
        from qgmodels import Customer, Order


        class OrderFactory(SQLAlchemyModelFactory):
            class Meta:
                model = Order
                sqlalchemy_session_persistence = "flush"

            @factory.lazy_attribute
            def customer_id(self):
                session = OrderFactory._meta.sqlalchemy_session
                stmt = select(Customer).where(Customer.name == "customer 1")
                return session.scalars(stmt).first().id
        """
    )
    project.makepyfile(
        test_a="""
        from qgmodels import make_sync_session
        from seeding import OrderFactory


        def test_seed():
            session = make_sync_session()
            OrderFactory._meta.sqlalchemy_session = session
            OrderFactory.create_batch(6)
        """
    )
    result = project.runpytest()
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(SUMMARY)
    result = project.runpytest("-o", "queryguard_test_code=fail")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*first at seeding.py:*in customer_id"])


def test_an_ignore_pattern_for_test_code_is_not_stale(project: Any) -> None:
    project.makepyfile(test_a=TEST_LOOP)
    project.makeini(
        "[pytest]\nqueryguard_ignore =\n    relationship-load * file=test_a.py\n"
    )
    result = project.runpytest()
    result.assert_outcomes(passed=1)
    result.stdout.no_fnmatch_line("*queryguard: stale ignores*")
    result.stdout.no_fnmatch_line("*in test code left out*")


def test_strict_mode_does_not_raise_in_test_code(project: Any) -> None:
    project.makepyfile(test_a=TEST_LOOP)
    project.runpytest("--queryguard-strict").assert_outcomes(passed=1)
    result = project.runpytest("--queryguard-strict", "-o", "queryguard_test_code=fail")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*NPlusOneDetected*"])


def test_the_json_file_leaves_them_out(project: Any) -> None:
    project.makepyfile(test_a=TEST_LOOP)
    project.runpytest("--queryguard-json=qg.json").assert_outcomes(passed=1)
    data = json.loads((project.path / "qg.json").read_text())
    assert data["tests"]["test_a.py::test_loop"]["findings"] == []


def test_the_summary_adds_up_the_workers(project: Any) -> None:
    pytest.importorskip("xdist")
    project.makepyfile(
        test_a=TEST_LOOP, test_b=TEST_LOOP.replace("test_loop", "test_other")
    )
    result = project.runpytest("-n", "2", "-p", "no:cacheprovider")
    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(
        [
            "2 N+1 findings in test code left out (2 tests): *",
            "To see them: *",
            "Why: *",
            "  test_a.py::test_loop",
            "  test_b.py::test_other",
        ]
    )


# ------------------------------------------------- calls on separate lines


def test_lookups_on_separate_lines_are_no_loop(project: Any) -> None:
    project.makepyfile(
        test_a="""
        from qgmodels import make_sync_session
        from shop import lookup


        def test_lines():
            session = make_sync_session()
            lookup(session, "c1")
            lookup(session, "c2")
            lookup(session, "c3")
            lookup(session, "c4")
            lookup(session, "c5")
        """
    )
    project.runpytest().assert_outcomes(passed=1)


@pytest.mark.parametrize(
    ("body", "reported"),
    [
        ("    for name in NAMES:\n        lookup(session, name)", True),
        ("    lookups(session, NAMES)", True),
        # four passes of a loop and a run on a line of its own: four runs
        ("    lookup(session, 'c0')\n    lookups(session, NAMES[:4])", False),
    ],
    ids=["loop-in-the-test", "loop-in-the-application", "loop-and-one-line"],
)
def test_a_loop_is_reported(project: Any, body: str, reported: bool) -> None:
    project.makepyfile(
        test_a="from qgmodels import make_sync_session\n"
        "from shop import lookup, lookups\n\n"
        "NAMES = ['c1', 'c2', 'c3', 'c4', 'c5']\n\n\n"
        "def test_loop():\n"
        "    session = make_sync_session()\n" + body + "\n"
    )
    result = project.runpytest()
    if not reported:
        result.assert_outcomes(passed=1)
        return
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*the same statement ran 5 times*"])


REQUESTS = """
from qgmodels import make_sync_session
from shop import order_page


def test_requests():
    session = make_sync_session()
    assert order_page(session, 10)
    assert order_page(session, 11)
    assert order_page(session, 20)
    assert order_page(session, 21)
    assert order_page(session, 30)
"""


def test_requests_on_separate_lines_share_a_session(project: Any) -> None:
    project.makepyfile(test_a=REQUESTS)
    project.runpytest().assert_outcomes(passed=1)


def test_strict_mode_counts_the_lazy_loads_of_all_lines(project: Any) -> None:
    project.makepyfile(test_a=REQUESTS)
    result = project.runpytest("--queryguard-strict")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*NPlusOneDetected*Order.customer*"])


def test_requests_in_a_loop_of_the_test_are_reported(project: Any) -> None:
    project.makepyfile(
        test_a="""
        from qgmodels import make_sync_session
        from shop import order_page


        def test_requests():
            session = make_sync_session()
            for order_id in [10, 11, 20, 21, 30]:
                assert order_page(session, order_id)
        """
    )
    result = project.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*Order.customer was lazy-loaded*"])


# ------------------------------------------------------------ the classes


def test_test_code_classifies_files(tmp_path: Path) -> None:
    root = tmp_path / "project"
    code = callsite.TestCode(str(root))
    code.add_files([str(root / "app" / "test_views.py")])
    frame = SimpleNamespace(f_globals={"__name__": "x"})

    def classify(*parts: str) -> bool:
        return code.classify(str(root.joinpath(*parts)), frame)  # type: ignore[arg-type]

    assert classify("app", "test_views.py")  # a collected test module
    assert classify("app", "conftest.py")
    assert classify("tests", "factories.py")
    assert classify("src", "app", "test", "helpers.py")
    assert not classify("app", "views.py")
    assert not classify("app", "testing.py")
    assert not classify("tests.py")  # a module, not a folder
    # a tests folder outside the rootdir
    assert not code.classify(str(tmp_path / "tests" / "app.py"), frame)  # type: ignore[arg-type]
    # the rootdir is the tests folder of a project: everything below it
    tests = tmp_path / "tests"
    tests.mkdir()
    client = str(tests / "utils" / "client.py")
    # ... not without a project root above it (an application copied to /tests)
    assert not callsite.TestCode(str(tests)).classify(client, frame)  # type: ignore[arg-type]
    (tmp_path / "pyproject.toml").write_text("")
    assert callsite.TestCode(str(tests)).classify(client, frame)  # type: ignore[arg-type]
    # ... and not when it is a project's root itself, a repository named "test"
    repo = tmp_path / "test"
    (repo / ".git").mkdir(parents=True)
    assert not callsite.TestCode(str(repo)).classify(
        str(repo / "app" / "views.py"),
        frame,  # type: ignore[arg-type]
    )


def test_a_factory_class_makes_its_module_test_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = type("BaseFactory", (), {})
    monkeypatch.setitem(sys.modules, "factory.base", SimpleNamespace(BaseFactory=base))
    own = type("UserFactory", (base,), {"__module__": "myfactories"})
    imported = type("UserFactory", (base,), {"__module__": "elsewhere"})
    assert callsite._defines_factory({"__name__": "myfactories", "U": own})
    assert not callsite._defines_factory({"__name__": "myfactories", "U": imported})
    assert not callsite._defines_factory({"__name__": "myfactories", "n": 1})


def test_find_origin_tells_the_test_lines_apart(tmp_path: Path) -> None:
    test_file = str(Path(__file__).resolve())
    code = callsite.TestCode(str(tmp_path))
    code.add_files([test_file])
    old = callsite.swap_test_code(code)
    try:
        here = callsite.find_origin()
        in_app = _app_call()
    finally:
        callsite.swap_test_code(old)
    # every frame is this test module: the test's own
    assert here.test_code and here.entry is None
    assert here.path is not None and here.path[0][0] == test_file
    # through a frame of another file: the application's, entered from here
    assert not in_app.test_code
    assert in_app.entry is not None
    assert all(filename == test_file for filename, *_ in in_app.entry)
    assert in_app.path is not None and in_app.path[0][0] != test_file


def _app_call() -> callsite.Origin:
    namespace: dict[str, Any] = {"callsite": callsite}
    exec(  # noqa: S102  # an application frame: a file that is no test code
        compile("def app():\n    return callsite.find_origin()\n", os.devnull, "exec"),
        namespace,
    )
    origin: callsite.Origin = namespace["app"]()
    return origin


def test_an_installed_application_called_from_the_test_is_reported(
    project: Any,
) -> None:
    # installed non-editable: its frames are in site-packages, not test code
    installed = project.path / "venv" / "lib" / "site-packages"
    installed.mkdir(parents=True)
    (installed / "installedshop.py").write_text(APP)
    project.makepyfile(
        test_a="""
        import os
        import sys
        # absolute: Python 3.10 keeps the import finder of a relative entry
        # across runs, so it would look in the previous test's directory
        sys.path.insert(0, os.path.abspath("venv/lib/site-packages"))
        from installedshop import customer_names
        from qgmodels import make_sync_session


        def test_app():
            assert customer_names(make_sync_session())
        """
    )
    result = project.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*Order.customer was lazy-loaded*"])
    result.stdout.no_fnmatch_line("*in test code left out*")


@pytest.mark.parametrize(
    "package", ["model_bakery", "model_mommy", "mixer", "django_dynamic_fixture"]
)
def test_an_installed_test_data_builder_is_a_test_tool(
    project: Any, package: str
) -> None:
    # model_bakery, mixer, django-dynamic-fixture: a loop of the test through
    # one of them is the test's own, as through factory_boy
    installed = project.path / "venv" / "lib" / "site-packages"
    (installed / package).mkdir(parents=True)
    (installed / package / "__init__.py").write_text(
        "from sqlalchemy import select\n"
        "from qgmodels import Customer\n\n\n"
        "def make(session, names):\n"
        "    for name in names:\n"
        "        session.scalars(select(Customer).where(Customer.name == name)).first()\n"
    )
    project.makepyfile(
        test_a=f"""
        import os
        import sys
        # absolute: Python 3.10 keeps the import finder of a relative entry
        # across runs, so it would look in the previous test's directory
        sys.path.insert(0, os.path.abspath("venv/lib/site-packages"))
        from {package} import make
        from qgmodels import make_sync_session


        def test_make():
            make(make_sync_session(), ["c1", "c2", "c3", "c4", "c5"])
        """
    )
    result = project.runpytest()
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(SUMMARY)


def test_a_test_code_finding_keeps_its_baseline_entry(project: Any) -> None:
    project.makepyfile(test_a=TEST_LOOP)
    run = ("--queryguard-baseline=qg.json",)
    project.runpytest(
        *run, "-o", "queryguard_test_code=fail", "--queryguard-update-baseline"
    ).assert_outcomes(passed=1)
    result = project.runpytest(*run)
    result.assert_outcomes(passed=1)
    result.stdout.no_fnmatch_line("*matched no finding*")


def test_an_installed_session_subclass_is_no_application(project: Any) -> None:
    # SQLModel's Session.exec() lives in site-packages: a loop of the test that
    # runs its queries through it is still the test's own
    installed = project.path / "venv" / "lib" / "site-packages"
    (installed / "fakesqlmodel").mkdir(parents=True)
    (installed / "fakesqlmodel" / "__init__.py").write_text(
        "from sqlalchemy.orm import Session as _Session\n\n\n"
        "class Session(_Session):\n"
        "    def exec(self, statement):\n"
        "        return self.execute(statement)\n"
    )
    project.makepyfile(
        test_a="""
        import os
        import sys
        # absolute: Python 3.10 keeps the import finder of a relative entry
        # across runs, so it would look in the previous test's directory
        sys.path.insert(0, os.path.abspath("venv/lib/site-packages"))
        from fakesqlmodel import Session
        from sqlalchemy import select
        from qgmodels import Customer, make_sync_session


        def test_loop():
            session = Session(make_sync_session().get_bind())
            for name in ["c1", "c2", "c3", "c4", "c5"]:
                session.exec(select(Customer).where(Customer.name == name)).first()
        """
    )
    result = project.runpytest()
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(SUMMARY)


def test_an_update_adds_no_test_code_finding_to_the_baseline(project: Any) -> None:
    project.makepyfile(test_a=TEST_LOOP)
    run = ("--queryguard-baseline=qg.json",)
    project.runpytest(*run, "--queryguard-update-baseline").assert_outcomes(passed=1)
    assert "test_loop" not in (project.path / "qg.json").read_text()
    # so fail reports it, instead of finding it in the baseline
    result = project.runpytest(*run, "-o", "queryguard_test_code=fail")
    result.assert_outcomes(failed=1)


def test_warn_gives_test_code_findings_their_patch(project: Any) -> None:
    project.makepyfile(test_a=TEST_LOOP)
    result = project.runpytest(
        "-o", "queryguard_test_code=warn", "--queryguard-write-patch=qg.patch"
    )
    result.assert_outcomes(passed=1, warnings=1)
    assert "joinedload(Order.customer)" in (project.path / "qg.patch").read_text()


def test_the_summary_names_five_tests_and_counts_the_rest() -> None:
    left_out = {f"test_x.py::test_{n}": 1 for n in range(7)}
    left_out["test_x.py::test_0"] = 3
    lines = format_test_code_summary(left_out)
    assert lines[0].startswith("9 N+1 findings in test code left out (7 tests): ")
    assert lines[1].startswith("To see them: pytest -o queryguard_test_code=warn")
    assert lines[3:] == [
        "  test_x.py::test_0",
        "  test_x.py::test_1",
        "  test_x.py::test_2",
        "  test_x.py::test_3",
        "  test_x.py::test_4",
        "  and 2 more",
    ]
