"""3rdparty_licenses.md must be one well-formed table that lists every package
in uv.lock and every GitHub Action the repository uses, once, with four columns."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    not (ROOT / "uv.lock").is_file(), reason="uv.lock is not in the source distribution"
)

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    pytest.skip("tomllib needs Python 3.11", allow_module_level=True)


HEADER = "| source | name | license type | license link |"
SEPARATOR = "|---|---|---|---|"


def _lines() -> list[str]:
    return (ROOT / "3rdparty_licenses.md").read_text(encoding="utf-8").splitlines()


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _rows() -> list[list[str]]:
    lines = _lines()
    start = lines.index(HEADER) + 2
    return [_cells(line) for line in lines[start:] if line.strip()]


def check_structure(lines: list[str]) -> None:
    """Raise AssertionError unless the lines are an intro and one table."""
    assert lines[0].startswith("# ")
    assert lines.count(HEADER) == 1, "the table header must appear once"
    header = lines.index(HEADER)
    assert lines[header + 1 : header + 2] == [SEPARATOR]
    # nothing that looks like a table line outside the table
    for number, line in enumerate(lines[:header], start=1):
        assert not line.lstrip().startswith("|"), f"line {number} is outside the table"
    table = lines[header + 2 :]
    # the table runs to the end of the file, without blank lines or prose in it
    while table and not table[-1].strip():
        table.pop()
    assert table
    for line in table:
        assert line.startswith("| https://"), f"not a table row: {line!r}"


def test_the_file_is_an_intro_followed_by_one_table() -> None:
    check_structure(_lines())


ROW = "| https://github.com/a/b | b | MIT | https://github.com/a/b/blob/main/LICENSE |"
GOOD = ["# Licenses", "", "Intro.", "", HEADER, SEPARATOR, ROW, ""]


@pytest.mark.parametrize(
    "broken",
    [
        ["# Licenses", "", ROW, "Intro.", "", HEADER, SEPARATOR, ROW],  # row above
        ["# Licenses", "", "Intro.", ""],  # no table
        [*GOOD[:-1], "", "A paragraph after the table."],
        [*GOOD[:6], ROW, "", ROW],  # a blank line splits the table
        [*GOOD[:5], ROW],  # no separator line
    ],
    ids=["row-above-header", "no-table", "prose-after", "split-table", "no-separator"],
)
def test_a_malformed_layout_is_rejected(broken: list[str]) -> None:
    check_structure(GOOD)
    with pytest.raises(AssertionError):
        check_structure(broken)


def test_every_row_has_four_filled_columns() -> None:
    rows = _rows()
    assert rows
    for row in rows:
        assert len(row) == 4 and all(row), row
        assert row[0].startswith("https://") and row[3].startswith("https://"), row


def test_no_component_is_listed_twice() -> None:
    names = [row[1].split(" (")[0].lower() for row in _rows()]
    assert len(names) == len(set(names))


def test_every_locked_package_is_listed() -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = {package["name"].lower() for package in lock["package"]}
    locked.discard("pytest-queryguard")
    listed = {row[1].split(" (")[0].split(" ")[0].lower() for row in _rows()}
    missing = sorted(name for name in locked if name not in listed)
    assert not missing, f"3rdparty_licenses.md does not list {missing}"


def _used_actions() -> set[str]:
    """owner/repo of every `uses:` in the workflows and in action/."""
    files = [*(ROOT / ".github" / "workflows").glob("*.yml")]
    files += [*(ROOT / "action").glob("*.yml")]
    used = set()
    for path in files:
        for ref in re.findall(r"uses:\s*(\S+)", path.read_text(encoding="utf-8")):
            if not ref.startswith("./"):
                owner, repo = ref.split("@")[0].split("/")[:2]
                used.add(f"{owner}/{repo}".lower())
    return used


@pytest.mark.skipif(
    not (ROOT / ".github" / "workflows").is_dir(),
    reason=".github is not in the source distribution",
)
def test_every_used_action_is_listed() -> None:
    used = _used_actions()
    assert {"actions/checkout", "pypa/gh-action-pypi-publish"} <= used
    listed = {
        row[0].removeprefix("https://github.com/").lower()
        for row in _rows()
        if row[0].startswith("https://github.com/")
    }
    missing = sorted(used - listed)
    assert not missing, f"3rdparty_licenses.md does not list the actions {missing}"
