"""The documentation must stay in step with the code.

The configuration reference has to name every ini option, command-line flag and
marker the plugin registers, and the site has to build without broken links.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


def _help(pytester: pytest.Pytester, *args: str) -> str:
    return pytester.runpytest_subprocess(*args).stdout.str()


def test_every_ini_option_is_in_the_reference(pytester: pytest.Pytester) -> None:
    reference = (DOCS / "configuration.md").read_text(encoding="utf-8")
    names = set(
        re.findall(r"^\s+(queryguard_\w+) \(", _help(pytester, "--help"), re.MULTILINE)
    )
    assert len(names) >= 10, names  # the regexp still finds the options
    missing = sorted(name for name in names if f"`{name}`" not in reference)
    assert not missing, f"docs/configuration.md does not document {missing}"


def test_every_flag_is_in_the_reference(pytester: pytest.Pytester) -> None:
    reference = (DOCS / "configuration.md").read_text(encoding="utf-8")
    flags = set(re.findall(r"(--(?:no-)?queryguard-[\w-]+)", _help(pytester, "--help")))
    assert len(flags) >= 5, flags
    missing = sorted(flag for flag in flags if flag not in reference)
    assert not missing, f"docs/configuration.md does not document {missing}"


def test_every_marker_is_in_the_reference(pytester: pytest.Pytester) -> None:
    reference = (DOCS / "configuration.md").read_text(encoding="utf-8")
    markers = set(
        re.findall(r"@pytest\.mark\.((?:allow|max)_\w+)", _help(pytester, "--markers"))
    )
    assert markers >= {"max_queries", "max_db_time", "allow_nplusone"}, markers
    missing = sorted(m for m in markers if f"`{m}" not in reference)
    assert not missing, f"docs/configuration.md does not document {missing}"


def test_site_builds_in_strict_mode(tmp_path: Path) -> None:
    pytest.importorskip("markdown")
    pytest.importorskip("pygments")
    if sys.version_info < (3, 11):
        pytest.skip("scripts/build_docs.py reads docs/site.toml with tomllib")
    result = subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts" / "build_docs.py"), "--strict"]
        + ["--out", str(tmp_path / "site")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "site" / "index.html").is_file()
