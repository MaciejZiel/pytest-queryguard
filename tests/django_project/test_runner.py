"""``pytest_queryguard.django``: N+1 detection under ``manage.py test``.

Each test runs ``python -m django test`` in a subprocess on a project of its
own in ``tmp_path``: settings that extend the test project's, an application
module (``shop``) with the loops, and a package of unittest test cases. The
subprocess uses SQLite whatever ``QUERYGUARD_DJANGO_DB`` says: the runner is
the same on every database, and a server's test database is held by the outer
session.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured

from pytest_queryguard import django as qg_django

ROOT = Path(__file__).resolve().parents[2]

SHOP = """
from tests.django_project.library.models import Author, Book


def book_lines():
    return [f"{book.title} by {book.author.name}" for book in Book.objects.all()]


def book_lines_fixed():
    return [
        f"{book.title} by {book.author.name}"
        for book in Book.objects.select_related("author")
    ]


def authors_one_by_one(ids):
    return [Author.objects.get(pk=pk).name for pk in ids]
"""

CASES = """
import unittest

from django.test import TestCase

from pytest_queryguard import ignore
from shop import authors_one_by_one, book_lines, book_lines_fixed
from tests.django_project.library.models import Book
from tests.django_project.library.seed import seed


class Books(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed()
        book_lines()  # an N+1 outside the test: not measured

    def test_n_plus_one(self):
        self.assertEqual(len(book_lines()), 18)

    def test_clean(self):
        self.assertEqual(len(book_lines_fixed()), 18)

    def test_repeated(self):
        self.assertEqual(len(authors_one_by_one(range(1, 7))), 6)

    def test_in_block(self):
        with ignore("relationship-load"):
            self.assertEqual(len(book_lines()), 18)

    def test_loop_in_the_test(self):
        names = [book.author.name for book in Book.objects.all()]
        self.assertEqual(len(names), 18)

    def test_fails_on_its_own(self):
        book_lines()
        self.assertEqual(1, 2)

    @unittest.skip("skipped")
    def test_skipped(self):
        book_lines()
"""


def _project(tmp_path: Path, cases: str, queryguard: dict[str, Any] | None) -> Path:
    (tmp_path / "shop.py").write_text(SHOP)
    settings = [
        "from tests.django_project.settings import *  # noqa: F403",
        (
            'DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", '
            '"NAME": ":memory:"}}'
        ),
        'TEST_RUNNER = "pytest_queryguard.django.QueryGuardRunner"',
    ]
    if queryguard is not None:
        settings.append(f"QUERYGUARD = {queryguard!r}")
    (tmp_path / "qg_settings.py").write_text("\n".join(settings) + "\n")
    package = tmp_path / "cases"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "test_books.py").write_text(textwrap.dedent(cases))
    return tmp_path


def _run(
    project: Path, *args: str, command: tuple[str, ...] = ("-m", "django")
) -> subprocess.CompletedProcess[str]:
    """``python -m django test -v 2 *args`` in the project."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("QUERYGUARD_")}
    env["DJANGO_SETTINGS_MODULE"] = "qg_settings"
    env["PYTHONPATH"] = os.pathsep.join([str(project), str(ROOT)])
    # The tests rewrite qg_settings.py between runs, sometimes to a file of the
    # same size within the same second ('fail' -> 'warn'); a cached .pyc,
    # checked by mtime in seconds and size, would then be loaded stale.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONWARNINGS", None)
    return subprocess.run(
        [sys.executable, *command, "test", "-v", "2", *args],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def _outcomes(output: str) -> dict[str, str]:
    """test name -> ok, FAIL, ERROR or skipped, from the -v 2 lines."""
    found: dict[str, str] = {}
    for line in output.splitlines():
        if line.startswith("test_") and " ... " in line:
            name = line.split(" ", 1)[0]
            found[name] = line.rsplit(" ... ", 1)[1].split(" ", 1)[0]
    return found


def _failure(output: str, name: str) -> str:
    """The report of one failing test."""
    start = output.index(f"FAIL: {name} ")
    end = output.find("\n=====", start)
    return output[start : end if end != -1 else None]


def test_fail_mode_fails_the_n_plus_ones_of_the_application(tmp_path: Path) -> None:
    result = _run(_project(tmp_path, CASES, None))
    output = result.stderr
    assert result.returncode == 1, output
    assert _outcomes(output) == {
        "test_clean": "ok",
        "test_fails_on_its_own": "FAIL",
        "test_in_block": "ok",
        "test_loop_in_the_test": "ok",
        "test_n_plus_one": "FAIL",
        "test_repeated": "FAIL",
        "test_skipped": "skipped",
    }, output
    report = _failure(output, "test_n_plus_one")
    assert "AssertionError: queryguard: N+1 detected" in report
    assert "Book.author was lazy-loaded 18 times" in report
    assert "first at shop.py:6 in book_lines" in report
    assert 'Book.objects.select_related("author")' in report
    # the report is the message alone: no frame of the plugin
    assert "pytest_queryguard" not in report
    repeated = _failure(output, "test_repeated")
    assert "kind: repeated-statement" in repeated
    assert "in_bulk" in repeated
    # a test that fails on its own is reported for its own failure
    own = _failure(output, "test_fails_on_its_own")
    assert "1 != 2" in own
    assert "queryguard" not in own


def test_warn_mode_warns_and_passes(tmp_path: Path) -> None:
    project = _project(tmp_path, CASES, {"mode": "warn"})
    result = _run(project, "cases.test_books.Books.test_n_plus_one")
    assert result.returncode == 0, result.stderr
    assert "QueryGuardWarning: queryguard: N+1 detected" in result.stderr
    assert "Book.author was lazy-loaded 18 times" in result.stderr
    # at the test method
    assert "cases/test_books.py:18: QueryGuardWarning" in result.stderr


def test_warnings_survive_buffer_and_become_errors_with_w_error(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, CASES, {"mode": "warn"})
    label = "cases.test_books.Books.test_n_plus_one"
    buffered = _run(project, "--buffer", label)
    assert buffered.returncode == 0, buffered.stderr
    assert "Book.author was lazy-loaded 18 times" in buffered.stderr
    escalated = _run(
        project,
        label,
        command=("-W", "error:queryguard:UserWarning", "-m", "django"),
    )
    assert escalated.returncode == 1, escalated.stderr
    assert "test_n_plus_one (" in escalated.stderr
    assert "QueryGuardWarning: queryguard: N+1 detected" in escalated.stderr


def test_the_command_line_mode_wins_over_the_settings(tmp_path: Path) -> None:
    project = _project(tmp_path, CASES, {"mode": "fail"})
    label = "cases.test_books.Books.test_n_plus_one"
    warned = _run(project, "--queryguard-mode=warn", label)
    assert warned.returncode == 0, warned.stderr
    assert "QueryGuardWarning" in warned.stderr
    off = _run(project, "--queryguard-mode=off", label)
    assert off.returncode == 0, off.stderr
    assert "queryguard" not in off.stderr


def test_ignore_patterns_from_the_settings(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        CASES,
        {"ignore": ["relationship-load Book.author", "repeated-statement *"]},
    )
    result = _run(
        project,
        "cases.test_books.Books.test_n_plus_one",
        "cases.test_books.Books.test_repeated",
    )
    assert result.returncode == 0, result.stderr
    assert "queryguard" not in result.stderr


def test_test_code_and_fail_on(tmp_path: Path) -> None:
    label = "cases.test_books.Books.test_loop_in_the_test"
    project = _project(tmp_path, CASES, {"test_code": "fail"})
    result = _run(project, label)
    assert result.returncode == 1, result.stderr
    report = _failure(result.stderr, "test_loop_in_the_test")
    assert "first at cases/test_books.py:" in report
    (tmp_path / "qg_settings.py").write_text(
        (tmp_path / "qg_settings.py")
        .read_text()
        .replace(
            "QUERYGUARD = {'test_code': 'fail'}",
            "QUERYGUARD = {'test_code': 'warn'}",
        )
    )
    warned = _run(project, label)
    assert warned.returncode == 0, warned.stderr
    assert "QueryGuardWarning" in warned.stderr
    # fail_on: a repeated statement only warns
    (tmp_path / "qg_settings.py").write_text(
        (tmp_path / "qg_settings.py")
        .read_text()
        .replace(
            "QUERYGUARD = {'test_code': 'warn'}",
            "QUERYGUARD = {'fail_on': ['relationship-load']}",
        )
    )
    mixed = _run(
        project,
        "cases.test_books.Books.test_n_plus_one",
        "cases.test_books.Books.test_repeated",
    )
    assert _outcomes(mixed.stderr) == {
        "test_n_plus_one": "FAIL",
        "test_repeated": "ok",
    }, mixed.stderr
    assert "kind: repeated-statement" in mixed.stderr


PARALLEL = """
from django.test import TestCase, TransactionTestCase

from shop import book_lines, book_lines_fixed
from tests.django_project.library.seed import seed


class First(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed()

    def test_n_plus_one(self):
        self.assertEqual(len(book_lines()), 18)

    def test_clean(self):
        self.assertEqual(len(book_lines_fixed()), 18)


class Second(TransactionTestCase):
    def setUp(self):
        seed()

    def test_n_plus_one_too(self):
        self.assertEqual(len(book_lines()), 18)

    def test_clean_too(self):
        self.assertEqual(len(book_lines_fixed()), 18)
"""


@pytest.mark.parametrize("start_method", ["fork", "spawn"])
def test_parallel(tmp_path: Path, start_method: str) -> None:
    if start_method == "fork" and sys.platform != "linux":
        pytest.skip("fork is the start method on Linux only")
    project = _project(tmp_path, PARALLEL, {"mode": "fail"})
    # the start method, set before Django's runner starts its pool
    (tmp_path / "start.py").write_text(
        "import multiprocessing, runpy, sys\n"
        "if __name__ == '__main__':  # not again in a spawned worker\n"
        f"    multiprocessing.set_start_method({start_method!r}, force=True)\n"
        "    sys.argv = ['django', *sys.argv[1:]]\n"
        "    runpy.run_module('django', run_name='__main__')\n"
    )

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return _run(project, "--parallel=2", *args, command=("start.py",))

    result = run()
    output = result.stderr
    assert result.returncode == 1, output
    assert "(2 processes)" in output or "Cloning test database" in output, output
    assert _outcomes(output) == {
        "test_clean": "ok",
        "test_clean_too": "ok",
        "test_n_plus_one": "FAIL",
        "test_n_plus_one_too": "FAIL",
    }, output
    assert output.count("Book.author was lazy-loaded 18 times") == 2, output
    # the command line reaches the workers
    warned = run("--queryguard-mode=warn")
    assert warned.returncode == 0, warned.stderr
    assert warned.stderr.count("QueryGuardWarning: queryguard: N+1 detected") == 2
    # escalated to errors in the workers: sent back without a traceback,
    # which cannot be pickled without tblib
    escalated = _run(
        project,
        "--parallel=2",
        "--queryguard-mode=warn",
        command=("-W", "error:queryguard:UserWarning", "start.py"),
    )
    assert escalated.returncode == 1, escalated.stderr
    assert "tblib" not in escalated.stdout + escalated.stderr
    assert _outcomes(escalated.stderr) == {
        "test_clean": "ok",
        "test_clean_too": "ok",
        "test_n_plus_one": "ERROR",
        "test_n_plus_one_too": "ERROR",
    }, escalated.stderr


MIXIN = """
from django.test import TestCase

from pytest_queryguard.django import QueryGuardTestCase
from shop import book_lines
from tests.django_project.library.seed import seed


class Guarded(QueryGuardTestCase, TestCase):
    @classmethod
    def setUpTestData(cls):
        seed()

    def test_guarded(self):
        self.assertEqual(len(book_lines()), 18)


class Unguarded(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed()

    def test_unguarded(self):
        self.assertEqual(len(book_lines()), 18)
"""


def test_the_mixin_under_django_s_own_runner(tmp_path: Path) -> None:
    project = _project(tmp_path, MIXIN, None)
    settings = tmp_path / "qg_settings.py"
    settings.write_text(
        settings.read_text().replace(
            "pytest_queryguard.django.QueryGuardRunner",
            "django.test.runner.DiscoverRunner",
        )
    )
    result = _run(project)
    assert _outcomes(result.stderr) == {
        "test_guarded": "FAIL",
        "test_unguarded": "ok",
    }, result.stderr
    assert "Book.author was lazy-loaded 18 times" in result.stderr


def test_bad_settings_stop_the_run(tmp_path: Path) -> None:
    project = _project(tmp_path, CASES, {"mode": "loud"})
    result = _run(project)
    assert result.returncode != 0
    assert "ImproperlyConfigured: QUERYGUARD['mode'] must be one of" in result.stderr
    assert "Creating test database" not in result.stderr


# --- the settings, in this process ------------------------------------------


def test_read_config_defaults_match_the_plugin() -> None:
    config = qg_django.read_config(None)
    assert config == qg_django.Config()
    assert config.mode == "fail"
    assert config.relationship_threshold == 2
    assert config.repeat_threshold == 5
    assert config.test_code == "off"
    assert config.max_events == 10_000


def test_read_config_values_and_precedence() -> None:
    config = qg_django.read_config(
        {
            "mode": " WARN ",
            "repeat_threshold": "4",
            "small_repeat_threshold": 2,
            "write_loop_threshold": 0,
            "scope": "session",
            "test_code": "fail",
            "fail_on": "relationship-load\n# a comment\n",
            "ignore": ["repeated-statement *", ""],
            "max_events": 0,
        },
        {"mode": "off", "scope": None},
    )
    assert config.mode == "off"
    assert config.repeat_threshold == 4
    assert config.small_repeat_threshold == 2
    assert config.write_loop_threshold is None
    assert config.scope == "session"
    assert config.test_code == "fail"
    assert config.fail_on == ("relationship-load",)
    assert [rule.kind for rule in config.ignore] == ["repeated-statement"]
    assert config.max_events is None


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ([], "QUERYGUARD must be a dict"),
        ({"baseline": "x"}, "unknown option(s) 'baseline'"),
        ({"repeat_threshold": 1}, "must be an integer >= 2, got 1"),
        ({"repeat_threshold": True}, "must be an integer >= 2, got True"),
        ({"write_loop_threshold": 1}, "must be 0 or an integer >= 2"),
        ({"small_repeat_threshold": 5}, "must be lower than repeat_threshold (5)"),
        ({"scope": "request"}, "must be one of test, session"),
        ({"fail_on": ["no-such-kind"]}, "QUERYGUARD['fail_on']"),
        ({"fail_on": [1]}, "must be a list of strings"),
        ({"ignore": ["relationship-load"]}, "QUERYGUARD['ignore']"),
        ({"max_events": -1}, "must be an integer >= 0"),
    ],
)
def test_read_config_rejects(raw: Any, message: str) -> None:
    with pytest.raises(ImproperlyConfigured) as caught:
        qg_django.read_config(raw)
    assert message in str(caught.value)


def test_the_result_class_of_a_custom_test_runner_is_kept() -> None:
    import unittest

    class Result(unittest.TextTestResult):
        pass

    class TextRunner(unittest.TextTestRunner):
        resultclass = Result

    class Runner(qg_django.QueryGuardRunner):
        test_runner = TextRunner

    resultclass = Runner(verbosity=0).get_resultclass()
    assert issubclass(resultclass, Result)
    assert resultclass.startTest is not Result.startTest
