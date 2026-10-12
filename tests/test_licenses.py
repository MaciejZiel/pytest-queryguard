"""3rdparty_licenses.md must list every package in uv.lock, once, with four columns."""

from __future__ import annotations

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


def _rows() -> list[list[str]]:
    rows = []
    for line in (
        (ROOT / "3rdparty_licenses.md").read_text(encoding="utf-8").splitlines()
    ):
        if line.startswith("| http"):
            rows.append([cell.strip() for cell in line.strip("|").split("|")])
    return rows


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
