"""Small helpers for reading PostgreSQL statement text."""

from __future__ import annotations

import re

# Text that is copied unchanged when statements are rewritten or scanned:
# quoted strings and identifiers, dollar quotes and comments. An unterminated
# one runs to the end of the statement.
QUOTED = r"""
      (?<![\w$])[eE]'(?:[^'\\]|\\.|'')*'?     # E'...' with backslash escapes
    | '(?:[^']|'')*'?                         # '...'
    | "(?:[^"]|"")*"?                         # "quoted identifier"
    | (?<![\w$])\$\$.*?(?:\$\$|\Z)            # $$...$$
    | (?<![\w$])\$(?P<tag>[A-Za-z_]\w*)\$.*?(?:\$(?P=tag)\$|\Z)  # $tag$...$tag$
    | --[^\n]*                                # line comment
    | /\*.*?(?:\*/|\Z)                        # block comment
"""


def skip_prefix(sql: str, start: int = 0) -> int | None:
    """Index after leading whitespace, parentheses and comments, in linear time.

    None for an unterminated block comment. A loop rather than a regex: a
    nested quantifier there backtracked exponentially on long indentation.
    """
    i, n = start, len(sql)
    while i < n:
        if sql[i].isspace() or sql[i] == "(":
            i += 1
        elif sql.startswith("--", i):
            end = sql.find("\n", i)
            i = n if end < 0 else end + 1
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            if end < 0:
                return None
            i = end + 2
        else:
            break
    return i


_WRITE_VERBS = frozenset(("INSERT", "UPDATE", "DELETE"))


def is_write(sql: str) -> bool:
    """Whether the statement is an INSERT, UPDATE or DELETE.

    Leading comments (sqlcommenter tags, a note in a ``text()``) and
    whitespace are skipped, as for the index check.
    """
    if sql[:1] in _PREFIX_STARTS or sql[:1].isspace():
        start = skip_prefix(sql)
        if start is None:
            return False
        return sql[start : start + 6].upper() in _WRITE_VERBS
    return sql[:6].upper() in _WRITE_VERBS


_PREFIX_STARTS = frozenset("-/(")


_CONTROL_VERBS = frozenset(("BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE"))
_CONTROL_PAIRS = frozenset(("START", "SET"))
_WORD = re.compile(r"[A-Za-z]+")


def is_transaction_control(sql: str) -> bool:
    """Whether the statement only opens, ends or marks a transaction.

    BEGIN, COMMIT, ROLLBACK (also ``ROLLBACK TO SAVEPOINT``), SAVEPOINT,
    RELEASE [SAVEPOINT], START TRANSACTION and SET TRANSACTION. Leading
    comments and whitespace are skipped. Other SET statements are not.
    """
    start = skip_prefix(sql)
    if start is None:
        return False
    first = _WORD.match(sql, start)
    if first is None:
        return False
    verb = first[0].upper()
    if verb in _CONTROL_VERBS:
        return True
    if verb in _CONTROL_PAIRS:
        after = skip_prefix(sql, first.end())
        second = _WORD.match(sql, after) if after is not None else None
        return second is not None and second[0].upper() == "TRANSACTION"
    return False
