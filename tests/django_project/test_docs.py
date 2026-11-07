"""The getting-started example of docs/django.md runs and fails as shown there."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOC = Path(__file__).resolve().parents[2] / "docs" / "django.md"

SETTINGS = """
SECRET_KEY = "docs"
INSTALLED_APPS = ["library"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"
USE_TZ = True
"""


def _blocks(language: str) -> list[str]:
    text = DOC.read_text(encoding="utf-8")
    return re.findall(rf"```{language}\n(.*?)```", text, re.DOTALL)


def test_the_getting_started_example(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the ini file names the settings, as in a project of its own
    monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)
    files = {}
    for block in _blocks("python"):
        first = block.splitlines()[0]
        if first.startswith("# ") and first.endswith(".py"):
            files[first[2:]] = block
    assert set(files) == {"library/models.py", "tests/test_books.py"}
    for name, source in files.items():
        path = pytester.path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    (pytester.path / "library" / "__init__.py").write_text("")
    (pytester.path / "tests" / "__init__.py").write_text("")
    (pytester.path / "settings.py").write_text(SETTINGS)
    pytester.makeini("[pytest]\nDJANGO_SETTINGS_MODULE = settings\npythonpath = .\n")
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-p", "no:randomly")
    result.assert_outcomes(failed=1)
    (shown,) = _blocks("text")
    out = result.stdout.str()
    for line in shown.splitlines():
        assert line in out, line
