"""The baseline file: findings a project knows about and accepts for now.

A baseline lists the identities (``Finding.identity``: kind, label, file and
function) of findings that existed when it was written. A finding on the list
does not fail or warn; anything else is reported as usual. The file holds no
line numbers and no counts, so editing code above a finding or loading a few
more rows does not invalidate an entry.

Labels that carry SQL or a table name are kept and compared in a portable form
(``portable``): without schema qualifiers, identifier quotes, the placeholder
style of the driver and SQLAlchemy 2.0's column aliases, so one file matches
on SQLite and PostgreSQL, and on SQLAlchemy 2.0 and 2.1. A file written before
that, with the label as the finding has it, reads into the same form.
"""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .fileio import atomic_write_text
from .findings import KIND_MISSING_INDEX, KIND_REPEAT, KIND_WRITE_LOOP, Finding

SCHEMA_VERSION = 1

Identity = tuple[str, str, str | None, str | None]


# What a driver writes for a bound parameter: pyformat, format, named and
# numeric (qmark, ?, is the form kept), and a PostgreSQL cast right after one.
_PARAMETER = re.compile(
    r"(?:%\(\w+\)s|%s|(?<![:\w]):[A-Za-z_]\w*|\$\d+|\?)(?:::\w+(?:\[\])*)?"
)
# A quoted identifier: "name" (standard) or `name` (MySQL). String literals
# are already "?" in a normalized statement.
_QUOTED = re.compile(r'"((?:[^"]|"")*)"|`((?:[^`]|``)*)`')
# A dotted name: table.column, schema.table or schema.table.column.
_DOTTED = re.compile(r"(?<![\w.$])[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+(?![\w$])")
# The words before a table name: the name after them is a table, so the first
# part of a dotted one there is a schema.
_TABLE_BEFORE = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+$", re.IGNORECASE)
_TABLE_NAME = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+([A-Za-z_]\w*)(?![\w.$])", re.IGNORECASE
)
# SQLAlchemy 2.0 labels each ORM column, ``customer.id AS customer_id``; 2.1
# writes ``customer.id``.
_COLUMN_ALIAS = re.compile(r"(?<![\w.$])([A-Za-z_]\w*\.[A-Za-z_]\w*) AS \w+")
_TABLE_PART = re.compile(r'"(?:[^"]|"")*"|`(?:[^`]|``)*`|[^."`]+')


def _unquote(match: re.Match[str]) -> str:
    double: str | None = match.group(1)
    if double is not None:
        return double.replace('""', '"')
    back: str = match.group(2)
    return back.replace("``", "`")


def portable_sql(sql: str) -> str:
    """A normalized statement as a label that does not depend on the database.

    The schema of a table, identifier quotes, the driver's placeholder style
    (with a PostgreSQL cast on a parameter) and SQLAlchemy 2.0's column
    aliases are left out. Applying it again changes nothing.
    """
    text = _PARAMETER.sub("?", sql)
    text = _QUOTED.sub(_unquote, text)
    chains = list(_DOTTED.finditer(text))
    schemas: set[str] = set()
    tables = {m.group(1) for m in _TABLE_NAME.finditer(text)}
    # the word before a name is near: whitespace is collapsed in a label
    at_table = [
        _TABLE_BEFORE.search(text, max(0, m.start() - 32), m.start()) is not None
        for m in chains
    ]
    for match, table in zip(chains, at_table, strict=True):
        parts = match.group().split(".")
        if table:
            schemas.update(parts[:-1])
            tables.add(parts[-1])
        else:
            schemas.update(parts[:-2])
    out = []
    end = 0
    for match, table in zip(chains, at_table, strict=True):
        parts = match.group().split(".")
        if table:
            parts = parts[-1:]
        elif len(parts) > 2:
            parts = parts[-2:]
        elif parts[0] in schemas and parts[0] not in tables:
            parts = parts[1:]
        out.append(text[end : match.start()])
        out.append(".".join(parts))
        end = match.end()
    out.append(text[end:])
    return _COLUMN_ALIAS.sub(r"\1", "".join(out))


def portable_table(label: str) -> str:
    """A table label (``"scheduler"."job"``, ``scheduler.job``) without its schema."""
    parts: list[str] = _TABLE_PART.findall(label)
    if not parts:
        return label
    last = parts[-1]
    if len(last) >= 2 and last[0] == last[-1] and last[0] in '"`':
        quote = last[0]
        return last[1:-1].replace(quote * 2, quote)
    return last


def portable(identity: Identity) -> Identity:
    """The identity as the baseline keeps and compares it (see the module doc)."""
    kind, label, file, function = identity
    if kind in (KIND_REPEAT, KIND_WRITE_LOOP):
        label = portable_sql(label)
    elif kind == KIND_MISSING_INDEX:
        label = portable_table(label)
    return (kind, label, file, function)


def key(finding: Finding) -> Identity:
    """The identity of a finding as a baseline entry."""
    return portable(finding.identity)


class BaselineError(ValueError):
    """The baseline file cannot be used; the message says why."""


def _sort_key(identity: Identity) -> tuple[str, str, str, str, int, int]:
    kind, label, file, function = identity
    # None sorts before any string, and the order never depends on the run
    return (kind, label, file or "", function or "", file is None, function is None)


def sorted_identities(identities: Iterable[Identity]) -> list[Identity]:
    return sorted(set(identities), key=_sort_key)


def dumps(identities: Iterable[Identity]) -> str:
    """The file text for these identities: sorted, indented, ends with a newline.

    Labels are written in their portable form.
    """
    entries = [
        {"kind": kind, "label": label, "file": file, "function": function}
        for kind, label, file, function in sorted_identities(
            portable(identity) for identity in identities
        )
    ]
    document = {"version": SCHEMA_VERSION, "findings": entries}
    text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        pass
    else:
        return text
    # A path that is not valid UTF-8 reaches an identity as a lone surrogate
    # (surrogateescape), which UTF-8 cannot encode. \udcXX escapes are plain
    # ASCII and read back as the same string.
    return json.dumps(document, indent=2, ensure_ascii=True) + "\n"


def write(path: Path, identities: Iterable[Identity]) -> None:
    """Write the baseline atomically, with ``\\n`` line ends on every platform."""
    atomic_write_text(path, dumps(identities))


_ENTRY_KEYS = ("kind", "label", "file", "function")


def _check_misspelled_keys(entry: dict[Any, Any], where: str) -> None:
    """Reject an unknown key that looks like a missing identity key.

    Other unknown keys are ignored: a later release may add keys to an entry
    without a new version (docs/stability.md), and this one must still read
    the file. A misspelled ``file`` or ``function`` would otherwise be read as
    null, and the entry would quietly match nothing.
    """
    missing = [key for key in _ENTRY_KEYS if key not in entry]
    for key in sorted(str(k) for k in entry if k not in _ENTRY_KEYS):
        close = difflib.get_close_matches(key.lower(), missing, n=1, cutoff=0.75)
        if close:
            raise BaselineError(
                f"{where} has an unknown key {key!r}; did you mean {close[0]!r}?"
            )


def _entry_identity(entry: Any, index: int, path: Path) -> Identity:
    where = f"{path}: findings[{index}]"
    if not isinstance(entry, dict):
        raise BaselineError(f"{where} must be an object, got {entry!r}")
    _check_misspelled_keys(entry, where)
    for key in ("kind", "label"):
        if not isinstance(entry.get(key), str) or not entry[key]:
            raise BaselineError(f"{where}: {key!r} must be a non-empty string")
    for key in ("file", "function"):
        value = entry.get(key)
        if value is not None and not isinstance(value, str):
            raise BaselineError(f"{where}: {key!r} must be a string or null")
    return portable(
        (entry["kind"], entry["label"], entry.get("file"), entry.get("function"))
    )


# A baseline is four levels deep; the limit only has to stay far from the
# interpreter's own recursion limit, which differs per Python version and platform.
_MAX_NESTING = 100
_NESTING_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[\[{]|[\]}]', re.DOTALL)


def _too_deep(text: str) -> bool:
    """Whether arrays or objects nest past the limit, found without recursion."""
    depth = 0
    for match in _NESTING_TOKEN.finditer(text):
        token = match.group()
        if token in "[{":
            depth += 1
            if depth > _MAX_NESTING:
                return True
        elif token in "]}":
            depth -= 1
    return False


def parse(text: str, path: Path) -> frozenset[Identity]:
    """The identities in the file text; raise BaselineError when it is invalid."""
    if _too_deep(text):
        # checked first: json would recurse, and the RecursionError it ends in can
        # surface again later (as an unraisable exception) on some interpreters
        raise BaselineError(f"{path} is not a queryguard baseline (nested too deep)")
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
        (known if key(finding) in baseline else new).append(finding)
    return new, known
