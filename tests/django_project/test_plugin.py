"""The plugin end to end in a pytest-django session: report, strict mode, files.

Each test runs pytest in a subprocess on a test module of its own, with the
test project's settings, as a Django project runs its suite.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.conftest import INNER_ADDOPTS

ROOT = Path(__file__).resolve().parents[2]

SESSION = """
import pytest
from tests.django_project.library.models import Author, Book, Publisher
from tests.django_project.library.seed import seed


@pytest.fixture
def seeded(db):
    seed()


def test_n_plus_one(seeded):
    names = [book.author.name for book in Book.objects.all()]
    assert len(names) == 18


def test_clean(seeded):
    names = [book.author.name for book in Book.objects.select_related("author")]
    assert len(names) == 18


def test_writes(seeded):
    for i in range(3):
        Publisher.objects.create(name=f"p{i}")
"""


@pytest.fixture
def project(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> pytest.Pytester:
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "tests.django_project.settings")
    monkeypatch.setenv("QUERYGUARD_DJANGO_TEST_NAME", "test_qg_inner")
    monkeypatch.setenv(
        "PYTHONPATH", os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])
    )
    pytester.makepyfile(test_books=SESSION)
    return pytester


def _run(project: pytest.Pytester, *args: str) -> pytest.RunResult:
    return project.runpytest_subprocess(
        "-p", "no:cacheprovider", "-p", "no:randomly", *args
    )


def test_the_report_names_the_django_fix(project: pytest.Pytester) -> None:
    result = _run(project)
    result.assert_outcomes(passed=2, failed=1)
    out = result.stdout.str()
    assert "test_n_plus_one" in out
    assert "Book.author was lazy-loaded 18 times" in out
    assert 'Book.objects.select_related("author")' in out
    assert "parents loaded at test_books.py:12 in test_n_plus_one" in out


def test_write_loops_when_switched_on(project: pytest.Pytester) -> None:
    result = _run(project, "-o", "queryguard_write_loop_threshold=2", "-k", "writes")
    result.assert_outcomes(failed=1, deselected=2)
    assert "bulk_create(rows)" in result.stdout.str()


def test_strict_mode_raises_at_the_access(project: pytest.Pytester) -> None:
    result = _run(project, "--queryguard-strict", "-k", "n_plus_one")
    result.assert_outcomes(failed=1, deselected=2)
    out = result.stdout.str()
    assert "queryguard strict: Book.author was lazy-loaded for 2 parents" in out
    assert "test_books.py:12: NPlusOneDetected" in out
    assert "During handling of the above exception" not in out


def test_baseline_json_and_sarif(project: pytest.Pytester) -> None:
    baseline = project.path / "baseline.json"
    _run(project, f"--queryguard-baseline={baseline}", "--queryguard-update-baseline")
    entries = json.loads(baseline.read_text())
    assert "relationship-load" in json.dumps(entries)
    result = _run(
        project,
        f"--queryguard-baseline={baseline}",
        "--queryguard-json=out.json",
        "--queryguard-sarif=out.sarif",
    )
    result.assert_outcomes(passed=3)
    counts = json.loads((project.path / "out.json").read_text())
    assert counts["complete"] is True
    test = counts["tests"]["test_books.py::test_n_plus_one"]
    assert test["statements"] == 19
    assert test["by_verb"] == {"select": 19}
    sarif = json.loads((project.path / "out.sarif").read_text())
    assert sarif["runs"][0]["tool"]["driver"]["name"] == "pytest-queryguard"


def test_budgets_are_written_and_enforced(project: pytest.Pytester) -> None:
    budgets = project.path / "budgets.json"
    _run(
        project,
        "-k",
        "clean",
        f"--queryguard-budgets={budgets}",
        "--queryguard-update-budgets",
    )
    written = json.loads(budgets.read_text())
    assert "1" in json.dumps(written)
    project.makepyfile(
        test_books=SESSION.replace(
            'Book.objects.select_related("author")]',
            'Book.objects.select_related("author")]\n    Author.objects.count()',
        )
    )
    result = _run(project, "-k", "clean", f"--queryguard-budgets={budgets}")
    result.assert_outcomes(failed=1, deselected=2)
    assert "budget" in result.stdout.str()


def test_django_is_not_imported_for_sqlalchemy_alone(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)
    pytester.makepyfile(
        test_sa="""
import sys
from sqlalchemy import create_engine, text
from pytest_queryguard import record_queries


def test_sqlalchemy_only():
    engine = create_engine("sqlite://")
    with record_queries() as log, engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    assert log.count == 1
    assert "django" not in sys.modules
"""
    )
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-p", "no:django")
    result.assert_outcomes(passed=1)


def test_the_index_check(project: pytest.Pytester) -> None:
    project.makepyfile(
        test_titles="""
from tests.django_project.library.models import Book
from tests.django_project.library.seed import seed


def test_by_title(db):
    seed()
    assert Book.objects.filter(title="book 1.1").count() == 1
"""
    )
    result = _run(project, "--queryguard-index-check", "test_titles.py")
    result.assert_outcomes(failed=1)
    out = result.stdout.str()
    assert "missing-index" in out
    assert "library_book" in out


TEST_CASES = """
from django.test import TestCase, TransactionTestCase

from tests.django_project.library.models import Author, Publisher


class FlushedAfterEachTest(TransactionTestCase):
    # its tear-down flushes the database and emits post_migrate, which runs
    # the same content type and permission queries for every app
    def test_one_query(self):
        Publisher.objects.create(name="p")
        assert Publisher.objects.count() == 1


class InATransaction(TestCase):
    def test_n_plus_one_in_the_test_is_still_found(self):
        publisher = Publisher.objects.create(name="p")
        for i in range(3):
            Author.objects.create(name=f"a{i}", publisher=publisher)
        names = [a.publisher.name for a in Author.objects.all()]
        assert len(names) == 3
"""


def test_django_test_case_set_up_and_tear_down_are_not_measured(
    project: pytest.Pytester,
) -> None:
    project.makepyfile(test_cases=TEST_CASES)
    result = _run(project, "test_cases.py", "--queryguard-json=out.json")
    result.assert_outcomes(passed=1, failed=1)
    tests = json.loads((project.path / "out.json").read_text())["tests"]
    flushed = next(v for k, v in tests.items() if "FlushedAfterEachTest" in k)
    assert flushed["findings"] == []
    # the test's own two statements; not the flush, not post_migrate
    assert flushed["statements"] == 2
    found = next(v for k, v in tests.items() if "InATransaction" in k)
    assert [f["label"] for f in found["findings"]] == ["Author.publisher"]


TEST_CODE = """
from tests.django_project.library.models import Book
from tests.django_project.library.seed import seed


def test_loop_in_the_test(db):
    seed()
    names = [book.author.name for book in Book.objects.all()]
    assert len(names) == 18


def test_loop_in_the_view(client, db):
    seed()
    assert client.get("/books/").status_code == 200
"""


def test_the_test_codes_n_plus_ones_are_left_out_by_default(
    project: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the default, queryguard_test_code = off (conftest sets fail)
    monkeypatch.setenv("PYTEST_ADDOPTS", INNER_ADDOPTS)
    project.makepyfile(test_code=TEST_CODE)
    result = _run(project, "test_code.py", "--queryguard-json=out.json")
    result.assert_outcomes(passed=1, failed=1)
    tests = json.loads((project.path / "out.json").read_text())["tests"]
    in_test = next(v for k, v in tests.items() if "in_the_test" in k)
    assert in_test["findings"] == []
    in_view = next(v for k, v in tests.items() if "in_the_view" in k)
    assert [f["label"] for f in in_view["findings"]] == ["Book.author"]


def test_the_test_codes_n_plus_ones_fail_with_fail(project: pytest.Pytester) -> None:
    project.makepyfile(test_code=TEST_CODE)
    result = _run(project, "test_code.py")
    result.assert_outcomes(failed=2)
