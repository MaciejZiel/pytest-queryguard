"""A snapshot of the Django surface that users can depend on.

The Django counterpart of ``tests/test_public_api.py``, which runs without
Django: ``pytest_queryguard.django`` (the ``manage.py test`` runner, its
settings and its command-line option), the labels of the findings Django
code produces, the fix each one names, and the Django versions and databases
the support is tested on. Since 1.1.0 the labels, fixes and versions follow
the deprecation policy (docs/stability.md#what-is-public); the runner is
still experimental (docs/stability.md#experimental), so a change to it may go
into a minor release. Either way, update the snapshot in the same commit, so
the change is visible in review.
"""

from __future__ import annotations

import argparse
import dataclasses
import inspect
import json
import re
import unittest
from pathlib import Path

import pytest
from django.test.runner import DiscoverRunner

import pytest_queryguard.django as qg_django
from pytest_queryguard.baseline import portable_sql
from pytest_queryguard.messages import make_findings
from tests.test_public_api import EXPECTED_INI

from .helpers import detections, labels, measure
from .library.models import Author, Book, Place, Tag

ROOT = Path(__file__).resolve().parents[2]

REL = "relationship-load"
REPEAT = "repeated-statement"
COLUMN = "column-load"

EXPECTED_ALL = ["QueryGuardRunner", "QueryGuardTestCase"]

# The label of a repeated get() as a baseline writes it (the same on every
# database); the full label is the SQL Django sends, normalized
REPEAT_LABEL = (
    "SELECT library_book.id, library_book.title, library_book.body, "
    "library_book.pages, library_book.author_id FROM library_book "
    "WHERE library_book.id = ? LIMIT ?"
)

# The QUERYGUARD setting: its keys, the ini options' names without the
# "queryguard_" prefix, and their defaults
EXPECTED_SETTINGS = {
    "mode": "fail",
    "relationship_threshold": 2,
    "repeat_threshold": 5,
    "small_repeat_threshold": None,
    "column_load_threshold": 5,
    "write_loop_threshold": None,
    "scope": "test",
    "test_code": "off",
    "fail_on": (),
    "ignore": (),
    "max_events": 10_000,
    "stack_depth": 3,
}

# Django and the databases the support is tested on (the "django" job of
# .github/workflows/ci.yml), as (Django, database)
EXPECTED_MATRIX = {
    ("5.2", "sqlite"),
    ("6.0", "sqlite"),
    ("6.1", "sqlite"),
    ("6.1", "postgresql"),
    ("5.2", "mysql"),
}


def test_all_is_the_documented_list() -> None:
    assert sorted(qg_django.__all__) == EXPECTED_ALL


def test_the_runner_is_a_discover_runner_with_its_option() -> None:
    assert issubclass(qg_django.QueryGuardRunner, DiscoverRunner)
    init = inspect.signature(qg_django.QueryGuardRunner.__init__)
    mode = init.parameters["queryguard_mode"]
    assert mode.kind is inspect.Parameter.KEYWORD_ONLY
    assert mode.default is None
    parser = argparse.ArgumentParser(exit_on_error=False)
    qg_django.QueryGuardRunner.add_arguments(parser)
    assert parser.parse_args([]).queryguard_mode is None
    for value in ("fail", "warn", "off"):
        parsed = parser.parse_args(["--queryguard-mode", value])
        assert parsed.queryguard_mode == value
    with pytest.raises((argparse.ArgumentError, SystemExit)):
        parser.parse_args(["--queryguard-mode", "strict"])


def test_the_test_case_mixin_wraps_run() -> None:
    assert str(inspect.signature(qg_django.QueryGuardTestCase.run)) == (
        "(self, result: 'unittest.TestResult | None' = None) -> 'Any'"
    )

    class Case(qg_django.QueryGuardTestCase, unittest.TestCase):
        def test_nothing(self) -> None:
            pass

    result = unittest.TestResult()
    Case("test_nothing").run(result)
    assert result.wasSuccessful()


def test_settings_keys_and_defaults() -> None:
    config = qg_django.read_config({})
    assert {
        f.name: getattr(config, f.name) for f in dataclasses.fields(config)
    } == EXPECTED_SETTINGS


def test_settings_defaults_are_the_ini_options_defaults() -> None:
    # tests/test_public_api.py snapshots the ini options; a key of QUERYGUARD
    # is one of them without the prefix, with the same default (0 for "off"
    # of a threshold, an empty list for a line list)
    def as_ini(value: object) -> str:
        return "0" if value is None else "[]" if value == () else str(value)

    # an unset queryguard_scope is "test"
    ini = {**EXPECTED_INI, "queryguard_scope": "test"}

    assert {
        f"queryguard_{key}": as_ini(value) for key, value in EXPECTED_SETTINGS.items()
    } == {f"queryguard_{key}": ini[f"queryguard_{key}"] for key in EXPECTED_SETTINGS}


@pytest.mark.allow_nplusone
@pytest.mark.usefixtures("seeded")
def test_labels_of_django_findings() -> None:
    with measure() as collector:
        [book.author.name for book in Book.objects.all()]  # forward foreign key
        [len(author.books.all()) for author in Author.objects.all()]  # reverse
        [author.profile.bio for author in Author.objects.all()]  # reverse 1:1
        [len(book.tags.all()) for book in Book.objects.all()]  # many-to-many
        [len(tag.books.all()) for tag in Tag.objects.all()]  # reverse m2m
        [hasattr(place, "restaurant") for place in Place.objects.all()]  # child
        [book.body for book in Book.objects.only("title")]  # deferred field
    assert labels(collector) == {
        (REL, "Book.author"),
        (REL, "Author.books"),
        (REL, "Author.profile"),
        (REL, "Book.tags"),
        (REL, "Tag.books"),
        (REL, "Place.restaurant"),
        (COLUMN, "Book.body"),
    }


@pytest.mark.allow_nplusone
@pytest.mark.usefixtures("seeded")
def test_the_fix_each_finding_names() -> None:
    # The code of the fix (the loader to add), not the wording around it
    with measure() as collector:
        [book.author.name for book in Book.objects.all()]
        [len(author.books.all()) for author in Author.objects.all()]
        [book.body for book in Book.objects.only("title")]
        [Book.objects.get(pk=pk).title for pk in range(1, 7)]
    messages = {f.label: f.message for f in make_findings(detections(collector))}
    assert 'Book.objects.select_related("author")' in messages["Book.author"]
    assert 'Author.objects.prefetch_related("books")' in messages["Author.books"]
    assert 'Book.objects.only(..., "body")' in messages["Book.body"]
    portable = {portable_sql(label): text for label, text in messages.items()}
    assert portable.keys() == {"Book.author", "Author.books", "Book.body", REPEAT_LABEL}
    assert "Model.objects.in_bulk(ids)" in portable[REPEAT_LABEL]
    assert "filter(pk__in=ids)" in portable[REPEAT_LABEL]


def test_supported_django_versions_and_databases() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^django = \["django>=5\.2"\]$', pyproject, re.MULTILINE)
    versions = sorted({version for version, _ in EXPECTED_MATRIX})
    listed = f"Django {', '.join(versions[:-1])} or {versions[-1]}"
    assert listed in (ROOT / "docs" / "django.md").read_text(encoding="utf-8")
    workflows = ROOT / ".github" / "workflows"
    if not workflows.is_dir():
        pytest.skip("the workflows are not in the sdist")
    ci = (workflows / "ci.yml").read_text(encoding="utf-8")
    full = re.search(r"'(\[\{\"django\".*?\])'", ci)
    assert full is not None
    tested = {(job["django"], job["db"]) for job in json.loads(full[1])}
    assert tested == EXPECTED_MATRIX
