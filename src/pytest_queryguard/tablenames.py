"""One way to match a table name against a pattern, for every place that takes one.

``queryguard_index_ignore_tables``, ``allow_missing_index``, ``queryguard_ignore``
and ``allow_nplusone(..., kind="missing-index")`` all go through
:func:`table_matches`, so a spelling that works in one works in the others.

A pattern is a table name or ``schema.table``. Each part is an identifier
written the way SQL writes it:

- unquoted (``UserAccount``, ``audit_*``): compared without regard to case, and
  ``*``, ``?`` and ``[...]`` are ``fnmatch`` wildcards;
- double-quoted (``"UserAccount"``, ``"my ""odd"" name"``): compared exactly,
  with no wildcards, and may hold dots.

A bare name matches the table in any schema. ``schema.table`` needs the schema
to be known, which it is on PostgreSQL, also for a table that the report prints
without its schema because it is on the search path. SQLite and MySQL name no
schema, so only bare names match there.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase


@dataclass(frozen=True, slots=True)
class _Part:
    text: str
    quoted: bool

    def matches(self, name: str) -> bool:
        if self.quoted:
            return self.text == name
        return fnmatchcase(name.lower(), self.text.lower())


def _split(pattern: str) -> list[_Part] | None:
    """The dotted parts of a pattern, or None when it is not well formed."""
    parts: list[_Part] = []
    i, n = 0, len(pattern)
    while True:
        if i < n and pattern[i] == '"':
            i += 1
            chars: list[str] = []
            while True:
                if i >= n:
                    return None  # unterminated quote
                if pattern[i] == '"':
                    if i + 1 < n and pattern[i + 1] == '"':
                        chars.append('"')
                        i += 2
                        continue
                    i += 1
                    break
                chars.append(pattern[i])
                i += 1
            parts.append(_Part("".join(chars), True))
        else:
            start = i
            while i < n and pattern[i] not in '."':
                i += 1
            if i == start or (i < n and pattern[i] == '"'):
                return None  # empty part, or a quote in the middle of a name
            parts.append(_Part(pattern[start:i], False))
        if i == n:
            return parts
        if pattern[i] != ".":
            return None
        i += 1


def table_matches(pattern: str, schema: str | None, table: str) -> bool:
    """Whether ``pattern`` names the table ``schema.table`` (``schema`` may be None)."""
    parts = _split(pattern.strip())
    if parts is not None:
        if len(parts) == 1:
            return parts[0].matches(table)
        if len(parts) == 2:
            return (
                schema is not None
                and parts[0].matches(schema)
                and parts[1].matches(table)
            )
    # What every place accepted before: the raw pattern as an fnmatch pattern
    # (case-sensitive) on the bare and on the qualified name.
    if fnmatchcase(table, pattern):
        return True
    return schema is not None and fnmatchcase(f"{schema}.{table}", pattern)
