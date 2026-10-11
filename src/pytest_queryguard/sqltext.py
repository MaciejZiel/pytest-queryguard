"""Small helpers for reading PostgreSQL statement text."""

from __future__ import annotations

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
