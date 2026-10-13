"""The baseline file: findings a project knows about and accepts for now.

A baseline lists the identities (``Finding.identity``: kind, label, file and
function) of findings that existed when it was written. A finding on the list
does not fail or warn; anything else is reported as usual. The file holds no
line numbers and no counts, so editing code above a finding or loading a few
more rows does not invalidate an entry.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .fileio import atomic_write_text
from .findings import Finding

SCHEMA_VERSION = 1

Identity = tuple[str, str, str | None, str | None]


class BaselineError(ValueError):
    """The baseline file cannot be used; the message says why."""


def _sort_key(identity: Identity) -> tuple[str, str, str, str, int, int]:
    kind, label, file, function = identity
    # None sorts before any string, and the order never depends on the run
    return (kind, label, file or "", function or "", file is None, function is None)


def sorted_identities(identities: Iterable[Identity]) -> list[Identity]:
    return sorted(set(identities), key=_sort_key)


def dumps(identities: Iterable[Identity]) -> str:
    """The file text for these identities: sorted, indented, ends with a newline."""
    entries = [
        {"kind": kind, "label": label, "file": file, "function": function}
        for kind, label, file, function in sorted_identities(identities)
    ]
    document = {"version": SCHEMA_VERSION, "findings": entries}
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def write(path: Path, identities: Iterable[Identity]) -> None:
    """Write the baseline atomically, with ``\\n`` line ends on every platform."""
    atomic_write_text(path, dumps(identities))


def _entry_identity(entry: Any, index: int, path: Path) -> Identity:
    where = f"{path}: findings[{index}]"
    if not isinstance(entry, dict):
        raise BaselineError(f"{where} must be an object, got {entry!r}")
    unknown = set(entry) - {"kind", "label", "file", "function"}
    if unknown:
        raise BaselineError(f"{where} has unknown keys: {', '.join(sorted(unknown))}")
    for key in ("kind", "label"):
        if not isinstance(entry.get(key), str) or not entry[key]:
            raise BaselineError(f"{where}: {key!r} must be a non-empty string")
    for key in ("file", "function"):
        value = entry.get(key)
        if value is not None and not isinstance(value, str):
            raise BaselineError(f"{where}: {key!r} must be a string or null")
    return (entry["kind"], entry["label"], entry.get("file"), entry.get("function"))


def parse(text: str, path: Path) -> frozenset[Identity]:
    """The identities in the file text; raise BaselineError when it is invalid."""
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise BaselineError(f"{path} is not valid JSON: {exc}") from None
    except RecursionError:
        # json raises it, not ValueError, for arrays or objects nested too deep
        raise BaselineError(
            f"{path} is not a queryguard baseline (nested too deep)"
        ) from None
    if not isinstance(document, dict) or "version" not in document:
        raise BaselineError(
            f"{path} is not a queryguard baseline (no 'version' field); "
            "regenerate it with --queryguard-update-baseline"
        )
    version = document["version"]
    if version != SCHEMA_VERSION or isinstance(version, bool):
        raise BaselineError(
            f"{path} has version {version!r}, but this pytest-queryguard reads "
            f"version {SCHEMA_VERSION}; upgrade the plugin or regenerate the file "
            "with --queryguard-update-baseline"
        )
    entries = document.get("findings")
    if not isinstance(entries, list):
        raise BaselineError(f"{path}: 'findings' must be a list")
    return frozenset(_entry_identity(entry, i, path) for i, entry in enumerate(entries))


def load(path: Path) -> frozenset[Identity]:
    """Read the baseline file; raise BaselineError when it is missing or invalid."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise BaselineError(
            f"baseline file {path} does not exist; create it with "
            "--queryguard-update-baseline"
        ) from None
    except (OSError, UnicodeDecodeError) as exc:
        raise BaselineError(f"cannot read baseline file {path}: {exc}") from None
    return parse(text, path)


def split(
    findings: Iterable[Finding], baseline: frozenset[Identity]
) -> tuple[list[Finding], list[Finding]]:
    """(findings not in the baseline, findings in it)."""
    new: list[Finding] = []
    known: list[Finding] = []
    for finding in findings:
        (known if finding.identity in baseline else new).append(finding)
    return new, known
