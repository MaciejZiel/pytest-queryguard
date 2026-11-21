"""Small helpers for reading statement text, and the shapes of snapshots."""

from __future__ import annotations

import functools
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


_CONTROL_VERBS = frozenset(("COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE"))
_CONTROL_PAIRS = frozenset(("START", "SET"))
# The words that may follow BEGIN when it starts a transaction (PostgreSQL,
# SQLite, MySQL, SQL Server). Any other word starts a block that does work,
# such as an Oracle or SQL Server ``BEGIN proc(:x); END;``, which a loop can
# repeat like any query.
_BEGIN_MODIFIERS = frozenset(
    (
        "TRANSACTION",
        "TRAN",
        "WORK",
        "DEFERRED",
        "IMMEDIATE",
        "EXCLUSIVE",
        "ISOLATION",
        "READ",
        "NOT",
        "DEFERRABLE",
        "DISTRIBUTED",
    )
)
_WORD = re.compile(r"[A-Za-z]+")


def first_word(sql: str) -> str | None:
    """The first keyword, upper case, after leading comments and whitespace."""
    start = skip_prefix(sql)
    if start is None:
        return None
    word = _WORD.match(sql, start)
    return None if word is None else word[0].upper()


def is_transaction_control(sql: str) -> bool:
    """Whether the statement only opens, ends or marks a transaction.

    BEGIN, COMMIT, ROLLBACK (also ``ROLLBACK TO SAVEPOINT``), SAVEPOINT,
    RELEASE [SAVEPOINT], START TRANSACTION and SET TRANSACTION. Leading
    comments and whitespace are skipped. Other SET statements are not, and
    neither is a BEGIN that opens a block (``BEGIN proc(:x); END;``).
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
    if verb == "BEGIN":
        after = skip_prefix(sql, first.end())
        if after is None:
            return False
        rest = sql[after:].strip()
        if rest in ("", ";"):
            return True
        second = _WORD.match(sql, after)
        return second is not None and second[0].upper() in _BEGIN_MODIFIERS
    if verb in _CONTROL_PAIRS:
        after = skip_prefix(sql, first.end())
        second = _WORD.match(sql, after) if after is not None else None
        return second is not None and second[0].upper() == "TRANSACTION"
    return False


# --- Statement shapes for snapshots (QueryLog.snapshot()) ---------------------

# One token of a statement: (kind, text, space before it). Kinds: "w" a word,
# "n" a quoted plain name without its quotes (never a keyword), "i" any other
# quoted identifier, "v" a value (a literal or a parameter), "p" punctuation
# or an operator. A parenthesised
# group is a list of tokens, with the space before its "(".
_TOKEN = re.compile(
    r"""
      (?P<space>\s+)
    | (?P<comment>--[^\n]*|/\*.*?(?:\*/|\Z))
    | (?P<value>
          (?<![\w$])[eE]'(?:[^'\\]|\\.|'')*'?    # E'...': backslash escapes
        | (?<![\w$])[nNxXbB]?'(?:[^']|'')*'?
        | (?<![\w$])0[xX][0-9a-fA-F]+(?![\w$])
        | (?<![\w$])\$\$.*?(?:\$\$|\Z)
        | (?<![\w$])\$(?P<tag>[A-Za-z_]\w*)\$.*?(?:\$(?P=tag)\$|\Z)
        | %\(\w+\)s | %s | \?\d* | \$\d+
        | (?<![:\w]):[A-Za-z_]\w*
        | (?<![\w.])(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?(?![\w.])
      )
    | (?P<ident>"(?:[^"]|"")*"?|`(?:[^`]|``)*`?|(?<![\w\])])\[[^\]]*\]?)
    | (?P<word>[A-Za-z_][\w$]*|\d\w*)
    | (?P<punct>::|.)
    """,
    re.VERBOSE | re.DOTALL,
)
_PLAIN_NAME = re.compile(r"\w+")
_CLOSING = {'"': '"', "`": "`", "[": "]"}

# The clause keywords at the top level of a statement or subquery, and how the
# text after each is shown: "..." (collapsed), table names ("tables"), the
# names of common table expressions ("with"), kept as written ("keep"), or
# nothing expected ("bare": what follows is shown as written).
_CLAUSES: dict[tuple[str, ...], str] = {
    ("SELECT",): "...",
    ("FROM",): "tables",
    ("WHERE",): "...",
    ("GROUP", "BY"): "...",
    ("HAVING",): "...",
    ("WINDOW",): "...",
    ("ORDER", "BY"): "...",
    ("LIMIT",): "limit",
    ("OFFSET",): "limit",
    ("FETCH",): "limit",
    ("FOR",): "keep",
    ("UNION",): "bare",
    ("UNION", "ALL"): "bare",
    ("INTERSECT",): "bare",
    ("INTERSECT", "ALL"): "bare",
    ("EXCEPT",): "bare",
    ("EXCEPT", "ALL"): "bare",
    ("WITH",): "with",
    ("WITH", "RECURSIVE"): "with",
    ("INSERT", "INTO"): "tables",
    ("INSERT", "IGNORE", "INTO"): "tables",
    ("INSERT", "OR", "REPLACE", "INTO"): "tables",
    ("INSERT", "OR", "IGNORE", "INTO"): "tables",
    ("REPLACE", "INTO"): "tables",
    ("VALUES",): "values",
    ("DEFAULT", "VALUES"): "bare",
    ("UPDATE",): "tables",
    ("SET",): "...",
    ("DELETE", "FROM"): "tables",
    ("RETURNING",): "...",
    ("ON", "CONFLICT"): "...",
    ("DO", "NOTHING"): "bare",
    ("DO", "UPDATE", "SET"): "...",
    ("ON", "DUPLICATE", "KEY", "UPDATE"): "...",
    ("ON",): "...",
    ("USING",): "...",
}
_CLAUSES[("JOIN",)] = "tables"
for _join in ("INNER", "CROSS", "NATURAL", "LEFT", "RIGHT", "FULL"):
    _CLAUSES[(_join, "JOIN")] = "tables"
    if _join in ("LEFT", "RIGHT", "FULL"):
        _CLAUSES[(_join, "OUTER", "JOIN")] = "tables"
_CLAUSE_LENGTHS = sorted({len(k) for k in _CLAUSES}, reverse=True)
# Clauses in which VALUES is part of the text, as MySQL's VALUES(col) in an
# ON DUPLICATE KEY UPDATE, not a clause of its own.
_NO_VALUES = frozenset(("SET", "DO UPDATE SET", "ON DUPLICATE KEY UPDATE"))
_SHAPED_VERBS = frozenset(
    ("SELECT", "INSERT", "UPDATE", "DELETE", "WITH", "REPLACE", "VALUES")
)
_KEEP_UPPER = frozenset(
    (
        "UPDATE",
        "SHARE",
        "NO",
        "KEY",
        "OF",
        "NOWAIT",
        "SKIP",
        "LOCKED",
        "AS",
        "NOT",
        "MATERIALIZED",
    )
)

_Token = tuple[str, str, bool]
_Tokens = list["_Token | _Group"]


class _Group(list["_Token | _Group"]):
    """The tokens inside one pair of parentheses."""

    space = False


def _tokenize(sql: str) -> _Tokens:
    root: _Tokens = []
    stack = [root]
    space = False
    for match in _TOKEN.finditer(sql):
        kind = match.lastgroup
        text = match.group()
        if kind in ("space", "comment"):
            space = True
            continue
        if kind == "punct" and text == "(":
            group = _Group()
            group.space = space
            stack[-1].append(group)
            stack.append(group)
        elif kind == "punct" and text == ")":
            if len(stack) > 1:
                stack.pop()
        elif kind == "ident":
            closed = len(text) > 1 and text[-1] == _CLOSING[text[0]]
            inner = text[1:-1] if closed else ""
            name = inner if _PLAIN_NAME.fullmatch(inner) else None
            stack[-1].append(("n" if name else "i", name or text, space))
        else:
            stack[-1].append((kind[0], text, space))  # type: ignore[index]
        space = False
    return root


def _word(token: _Token | _Group) -> str | None:
    if isinstance(token, _Group) or token[0] != "w":
        return None
    return token[1].upper()


def _is_query(group: _Group) -> bool:
    return bool(group) and _word(group[0]) in ("SELECT", "WITH", "VALUES")


def _subquery(group: _Group) -> str:
    return f"({_shape(group)})" if _is_query(group) else "(...)"


def _plain(tokens: _Tokens, *, keywords: bool = False) -> str:
    """Tokens as written, with values as ``...`` and subqueries shaped.

    With ``keywords``, the words of FOR UPDATE and WITH are upper case.
    """
    out = ""
    value = False  # the last token was a value
    for token in tokens:
        if isinstance(token, _Group):
            piece, space = _subquery(token), token.space
        elif token[0] == "v":
            if value:
                continue  # one ``...`` for adjacent values, as in ``E'a' 'b'``
            piece, space = "...", token[2]
        else:
            piece, space = token[1], token[2]
            if keywords and token[0] == "w" and piece.upper() in _KEEP_UPPER:
                piece = piece.upper()
        if (
            out
            and (space or out.endswith(","))
            and piece not in (",", ".")
            and not out.endswith(".")
        ):
            out += " "
        out += piece
        value = not isinstance(token, _Group) and token[0] == "v"
    return out


def _tables(tokens: _Tokens) -> str:
    """Table names, without aliases; column lists and arguments as ``(...)``."""
    items: list[str] = []
    name = ""
    done = False
    for token in tokens:
        if not isinstance(token, _Group) and token[1] == ",":
            if name:
                items.append(name)
            name, done = "", False
        elif done or not name and _word(token) == "LATERAL":
            continue
        elif isinstance(token, _Group):
            if not name:
                name = f"({_shape(token)})"
            else:
                name += " (...)" if token.space else "(...)"
            done = True
        elif token[0] in ("w", "n", "i") and (not name or name.endswith(".")):
            name += token[1]
        elif token[1] == "." and name:
            name += "."
        elif name:
            done = True  # an alias, AS, an index hint: not shown
    if name:
        items.append(name)
    return ", ".join(items)


def _collapsed(tokens: _Tokens, clause: str) -> str:
    if clause == "SELECT" and _word(tokens[0]) == "DISTINCT":
        return "DISTINCT ..." if len(tokens) > 1 else "DISTINCT"
    return "..."


def _match_clause(tokens: _Tokens, i: int, current: str) -> tuple[str, int] | None:
    first = _word(tokens[i])
    if first is None:
        return None
    if first == "VALUES" and current in _NO_VALUES:
        return None
    if first == "WITH" and _CLAUSES.get(tuple(current.split())) == "limit":
        return None  # FETCH FIRST n ROWS WITH TIES
    before = tokens[i - 1] if i else None
    if before is not None and not isinstance(before, _Group):
        if before[1] == "." or _word(before) == "AS":
            return None  # a column or an alias with a keyword's name: t.offset
        if (
            first == "FROM"
            and _word(before) == "DISTINCT"
            and i > 1
            and _word(tokens[i - 2]) in ("IS", "NOT")
        ):
            return None  # IS [NOT] DISTINCT FROM
    for length in _CLAUSE_LENGTHS:
        words = tuple(_word(t) for t in tokens[i : i + length])
        if len(words) == length and words in _CLAUSES:
            return " ".join(words), length  # type: ignore[arg-type]
    return None


def _clauses(tokens: _Tokens) -> list[tuple[str, _Tokens]]:
    """The statement split at its clause keywords: (keyword, text after it)."""
    parts: list[tuple[str, _Tokens]] = [("", [])]
    i = 0
    while i < len(tokens):
        current = parts[-1][0]
        found = None if current.startswith("FOR") else _match_clause(tokens, i, current)
        if found is None:
            parts[-1][1].append(tokens[i])
            i += 1
        else:
            parts.append((found[0], []))
            i += found[1]
    return parts


def _shape(tokens: _Tokens) -> str:
    """The shape of one statement or subquery (see ``fingerprint``)."""
    out: list[str] = []
    paginated = False
    for keyword, rest in _clauses(tokens):
        mode = _CLAUSES[tuple(keyword.split())] if keyword else "bare"
        if mode == "limit":
            if not paginated:
                out.append("LIMIT ...")
            paginated = True
            continue
        paginated = False
        if mode == "tables":
            text = _tables(rest)
        elif mode == "...":
            text = _collapsed(rest, keyword) if rest else ""
        elif mode == "values":
            text = (
                "(...)"
                if rest and all(isinstance(t, _Group) or t[1] == "," for t in rest)
                else ("..." if rest else "")
            )
        else:  # bare, keep and with
            text = _plain(rest, keywords=mode != "bare")
        out.append(" ".join(p for p in (keyword, text) if p))
    return " ".join(p for p in out if p)


@functools.lru_cache(maxsize=2048)  # a suite repeats the same statement texts
def fingerprint(sql: str) -> str:
    """A short, stable text of what a statement does, without its values.

    SELECT, INSERT, UPDATE, DELETE and WITH statements keep their clause
    keywords and table names; the text of every other clause (the column
    list, the conditions, the values) becomes ``...``, so
    ``SELECT book.id, book.title FROM book WHERE book.id = ?`` is
    ``SELECT ... FROM book WHERE ...``. Table aliases are dropped, a column
    list or a function's arguments after a name are ``(...)``, a subquery in
    FROM or after UNION is shaped the same way, and LIMIT, OFFSET and FETCH
    together are ``LIMIT ...``. Other statements are kept as written, with
    each literal or parameter as ``...``. Everywhere, comments are dropped,
    whitespace is one space, and a quoted name made of letters, digits and
    underscores loses its quotes, so the text is the same on SQLite,
    PostgreSQL and MySQL when the ORM writes the same statement.
    """
    statements: list[_Tokens] = [[]]
    for token in _tokenize(sql):
        if not isinstance(token, _Group) and token[1] == ";":
            statements.append([])
        else:
            statements[-1].append(token)
    shapes = []
    for tokens in statements:
        if not tokens:
            continue
        head = tokens[0]
        if isinstance(head, _Group) or _word(head) in _SHAPED_VERBS:
            shapes.append(_shape(tokens))
        else:
            shapes.append(_plain(tokens))
    return "; ".join(shapes)
