"""Route budgets for requests of Django's test client, in a pytest-django session."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

REQUESTS = """
import pytest

pytestmark = pytest.mark.allow_nplusone


def test_one_book(client, seeded):
    for pk in (1, 2):
        assert client.get(f"/books/{pk}/").status_code == 200


def test_titles(client, seeded):
    assert client.get("/books/").status_code == 200


@pytest.mark.queryguard_route_budgets({"/books/<int:pk>/": 2}, default=None)
def test_marker(client, seeded):
    assert client.get("/books/1/").status_code == 200
    assert client.get("/books/").status_code == 200


def test_unresolved(client, seeded):
    assert client.get("/nowhere/").status_code == 404
"""

CONFTEST = """
import pytest
from tests.django_project.library.seed import seed


@pytest.fixture
def seeded(db):
    seed()
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
    pytester.makeconftest(CONFTEST)
    pytester.makepyfile(test_requests=REQUESTS)
    return pytester


def _run(project: pytest.Pytester, *args: str) -> pytest.RunResult:
    return project.runpytest_subprocess(
        "-p", "no:cacheprovider", "-p", "no:randomly", *args
    )


def test_a_django_request_over_its_route_budget(project: pytest.Pytester) -> None:
    result = _run(project, "-o", "queryguard_route_budgets=GET /books/<int:pk>/=1")
    result.assert_outcomes(passed=3, failed=1)
    result.stdout.fnmatch_lines(
        [
            (
                "*queryguard: route budget: GET /books/<int:pk>/ may run at most 1 "
                "statement per request, GET /books/1/ ran 2"
            ),
            '*(the limit is "GET /books/<int:pk>/" in queryguard_route_budgets)',
            "*2 requests of this route went over*",
            "*1. SELECT * (*views.py:* in one_book)",
        ]
    )


def test_the_default_budget_and_the_marker(project: pytest.Pytester) -> None:
    result = _run(project, "-o", "queryguard_route_default_budget=1")
    # /books/ runs a statement per book: over the default; the marker turns
    # the default off for its test; a request no route matched has none
    result.assert_outcomes(passed=2, failed=2)
    out = result.stdout.str()
    assert "GET /books/ may run at most 1 statement per request" in out
    assert "test_marker" not in out.split("short test summary")[-1]
    assert "/nowhere/" not in out


def test_without_route_budgets_nothing_fails(project: pytest.Pytester) -> None:
    _run(project).assert_outcomes(passed=4)
