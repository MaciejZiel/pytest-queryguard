"""The README must stay short, its links must work and its example must be real.

The 30-second example is the project in examples/sync-n-plus-one: the code
block must be that file, and the report block must be what pytest prints for
it (the SQL line is left out: SQLAlchemy 2.0 renders column aliases).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
EXAMPLE = ROOT / "examples" / "sync-n-plus-one"

pytestmark = [
    pytest.mark.allow_nplusone,
    # the source distribution ships README.md and docs/ but not examples/
    pytest.mark.skipif(not EXAMPLE.is_dir(), reason="examples/ is not available"),
]


def _blocks() -> list[str]:
    return re.findall(r"```[\w-]*\n(.*?)```", README, flags=re.DOTALL)


def test_readme_is_short() -> None:
    assert len(README.splitlines()) <= 300


def test_relative_links_point_to_files() -> None:
    missing = []
    for target in re.findall(r"\]\((?!https?://|#|mailto:)([^)\s]+)\)", README):
        path = target.split("#", 1)[0]
        if path and not (ROOT / path).exists():
            missing.append(target)
    assert not missing, missing


def test_quick_start_code_is_the_example() -> None:
    code = (EXAMPLE / "app" / "orders.py").read_text(encoding="utf-8")
    assert code in _blocks()
    assert "def test_list_orders(session):" in README


def test_quick_start_output_is_what_pytest_prints() -> None:
    (report,) = [b for b in _blocks() if b.startswith("queryguard: N+1 detected")]
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS"}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_orders.py::test_list_orders",
        ],
        cwd=EXAMPLE,
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=120,
    )
    assert result.returncode == 1, result.stdout
    wanted = [
        line for line in report.splitlines() if not line.lstrip().startswith("sql:")
    ]
    actual = result.stdout.splitlines()
    position = 0
    for line in wanted:
        assert line in actual[position:], (
            f"{line!r} is not in the output:\n{result.stdout}"
        )
        position = actual.index(line, position) + 1


def test_index_check_output_is_in_the_example_readme() -> None:
    (report,) = [b for b in _blocks() if b.startswith("queryguard: missing index")]
    example = (ROOT / "examples" / "postgres-missing-index" / "README.md").read_text(
        encoding="utf-8"
    )
    assert report in example


def test_getting_started_shows_the_examples_output() -> None:
    page = (ROOT / "docs" / "getting-started.md").read_text(encoding="utf-8")
    sync = (EXAMPLE / "README.md").read_text(encoding="utf-8")
    report = sync[sync.index("```\nFFF") : sync.index("(The timing numbers")]
    assert report.rstrip() in page
    async_example = (ROOT / "examples" / "async-fastapi" / "README.md").read_text(
        encoding="utf-8"
    )
    async_report = async_example[
        async_example.index("queryguard: N+1 detected") : async_example.index(
            "```\n\n`/orders-fixed`"
        )
    ]
    assert async_report.rstrip() in page
