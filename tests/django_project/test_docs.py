"""The Django examples of the docs run as the pages say: the getting-started
example of docs/django.md fails as shown there, and the examples of
"Collecting queries without failing the test" and of the scaling recipe pass."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[2] / "docs"
DOC = DOCS / "django.md"
SCALING = DOCS / "recipes-query-count-scaling.md"

SETTINGS = """
SECRET_KEY = "docs"
INSTALLED_APPS = ["library"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"
USE_TZ = True
"""


def _blocks(language: str, doc: Path = DOC) -> list[str]:
    text = doc.read_text(encoding="utf-8")
    return re.findall(rf"```{language}\n(.*?)```", text, re.DOTALL)


def _files(doc: Path) -> dict[str, str]:
    """The Python blocks of a page whose first line names their file."""
    files = {}
    for block in _blocks("python", doc):
        first = block.splitlines()[0]
        if first.startswith("# ") and first.endswith(".py"):
            assert first[2:] not in files, f"{doc.name} shows {first[2:]} twice"
            files[first[2:]] = block
    return files


def _project(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, files: dict[str, str]
) -> pytest.RunResult:
    # the ini file names the settings, as in a project of its own
    monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)
    for name, source in files.items():
        path = pytester.path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    (pytester.path / "library" / "__init__.py").write_text("")
    (pytester.path / "tests" / "__init__.py").write_text("")
    (pytester.path / "settings.py").write_text(SETTINGS)
    pytester.makeini("[pytest]\nDJANGO_SETTINGS_MODULE = settings\npythonpath = .\n")
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", "-p", "no:randomly")


def _pick(files: dict[str, str], *names: str) -> dict[str, str]:
    assert set(names) <= set(files), sorted(files)
    return {name: files[name] for name in names}


def test_the_pages_show_these_files() -> None:
    assert set(_files(DOC)) == {
        "library/models.py",
        "tests/test_books.py",
        "library/services.py",
        "tests/test_add_book.py",
    }
    assert set(_files(SCALING)) == {
        "library/reports.py",
        "tests/test_book_scaling.py",
    }


def test_the_getting_started_example(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = _pick(_files(DOC), "library/models.py", "tests/test_books.py")
    result = _project(pytester, monkeypatch, files)
    result.assert_outcomes(failed=1)
    (shown,) = _blocks("text")
    out = result.stdout.str()
    for line in shown.splitlines():
        assert line in out, line


def test_recording_without_failing(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = _pick(
        _files(DOC),
        "library/models.py",
        "library/services.py",
        "tests/test_add_book.py",
    )
    _project(pytester, monkeypatch, files).assert_outcomes(passed=1)


def test_the_scaling_recipe(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = {**_pick(_files(DOC), "library/models.py"), **_files(SCALING)}
    _project(pytester, monkeypatch, files).assert_outcomes(passed=4)


def test_the_scaling_recipe_fails_without_select_related(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = {**_pick(_files(DOC), "library/models.py"), **_files(SCALING)}
    reports = files["library/reports.py"]
    assert '.select_related("author")' in reports
    files["library/reports.py"] = reports.replace('.select_related("author")', ".all()")
    result = _project(pytester, monkeypatch, files)
    result.assert_outcomes(failed=4)
    out = result.stdout.str()
    for count in (2, 11, 51):
        assert f"expected exactly 1 query, got {count}" in out
    assert "{1: 2, 10: 11, 50: 51}" in out
