"""A parser for the condition text of an EXPLAIN plan node.

PostgreSQL's JSON plan gives the relation, alias, schema and index of a node as
fields, but its conditions (``Filter``, ``Index Cond``, ``Recheck Cond``) are
only deparsed SQL text: there is no structured form of a predicate in any
version from 14 to 18. This module turns that text into a small tree with a
tokenizer and a precedence-climbing parser, so that the index check reasons
about columns, casts, functions and operators instead of matching regular
expressions over the text.

The grammar is the part of SQL that ``ruleutils`` prints for plan conditions.
Anything outside it raises ``Unparsed`` inside the parser and comes out of
``parse()`` as ``None``; callers then fall back to their text matching.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache


class Unparsed(Exception):
    """The text uses syntax this parser does not know."""


@dataclass(frozen=True, slots=True)
class Col:
    """A column reference as printed: ``qualifier.name``, quotes included."""

    qualifier: str
    name: str


@dataclass(frozen=True, slots=True)
class Lit:
    """A literal, a parameter, a keyword constant or a sub-plan reference."""

    text: str


@dataclass(frozen=True, slots=True)
class Call:
    """A function call, ``ANY (...)``, ``ARRAY[...]`` or a row of values."""

    name: str
    args: tuple[Expr, ...]


@dataclass(frozen=True, slots=True)
class Cast:
    operand: Expr
    type_name: str  # lower case, single spaces: "text", "character varying(10)"


@dataclass(frozen=True, slots=True)
class Op:
    op: str  # "=", "<>", "->>", "IS DISTINCT FROM", ...
    left: Expr
    right: Expr


@dataclass(frozen=True, slots=True)
class Bool:
    op: str  # "AND" or "OR"
    items: tuple[Expr, ...]


@dataclass(frozen=True, slots=True)
class Not:
    operand: Expr


@dataclass(frozen=True, slots=True)
class Postfix:
    operand: Expr
    text: str  # "IS NULL", "IS NOT NULL", "IS TRUE", ...


Expr = Col | Lit | Call | Cast | Op | Bool | Not | Postfix

_TOKEN = re.compile(
    r"""
      (?P<ws>\s+)
    | (?P<str>E?'(?:[^']|'')*')
    | (?P<qid>"(?:[^"]|"")+")
    | (?P<param>\$\d+)
    | (?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
    | (?P<cast>::)
    | (?P<id>[^\W\d][\w$]*)
    | (?P<op>[-+*/<>=~!@\#%^&|?]+)
    | (?P<punct>[()\[\],.])
    """,
    re.VERBOSE,
)

_KEYWORDS = frozenset({"and", "or", "not", "is"})
_TYPE_STOP = frozenset(
    {"and", "or", "is", "not", "collate", "at", "from", "isnull", "notnull"}
    | {"in", "like", "ilike", "similar", "between", "then", "else", "end", "when"}
    | {"asc", "desc"}
)
_SUBPLAN = frozenset({"subplan", "initplan", "hashed", "alternatives"})
_OR, _AND, _NOT, _SYMBOL = 1, 2, 3, 4
_MAX_DEPTH = 40


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str
    text: str


def _tokenize(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    pos = 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if match is None:
            raise Unparsed(text[pos : pos + 10])
        pos = match.end()
        kind = match.lastgroup or ""
        if kind == "ws":
            continue
        if kind == "str" and text[match.start()] == "E" and "\\" in match[0]:
            raise Unparsed("escape string")
        tokens.append(_Token(kind, match[0]))
    return tokens


class _Parser:
    def __init__(self, tokens: list[_Token]) -> None:
        self.tokens = tokens
        self.pos = 0
        self.depth = 0

    # -- token helpers

    def peek(self, ahead: int = 0) -> _Token | None:
        index = self.pos + ahead
        return self.tokens[index] if index < len(self.tokens) else None

    def take(self) -> _Token:
        token = self.peek()
        if token is None:
            raise Unparsed("unexpected end")
        self.pos += 1
        return token

    def is_punct(self, text: str, ahead: int = 0) -> bool:
        token = self.peek(ahead)
        return token is not None and token.kind == "punct" and token.text == text

    def expect(self, text: str) -> None:
        if not self.is_punct(text):
            raise Unparsed(f"expected {text}")
        self.pos += 1

    def keyword(self, ahead: int = 0) -> str | None:
        token = self.peek(ahead)
        if token is not None and token.kind == "id" and token.text.lower() in _KEYWORDS:
            return token.text.lower()
        return None

    # -- grammar

    def parse(self) -> Expr:
        expr = self.expr(0)
        if self.peek() is not None:
            raise Unparsed("trailing text")
        return expr

    def expr(self, min_prec: int) -> Expr:
        self.depth += 1
        if self.depth > _MAX_DEPTH:
            raise Unparsed("too deep")
        left = self.unary()
        symbol: str | None = None
        while (token := self.peek()) is not None:
            word = self.keyword()
            if word == "or":
                prec = _OR
            elif word == "and":
                prec = _AND
            elif word == "is" or token.kind == "op":
                prec = _SYMBOL
            else:
                break
            if prec < min_prec:
                break
            self.pos += 1
            if word == "is":
                left = self.is_tail(left)
            elif word in ("and", "or"):
                right = self.expr(prec + 1)
                name = word.upper()
                if isinstance(left, Bool) and left.op == name:
                    left = Bool(name, (*left.items, right))
                else:
                    left = Bool(name, (left, right))
            else:
                # PostgreSQL parenthesizes every operator expression, so two
                # different operators at one level are not its output.
                if symbol not in (None, token.text):
                    raise Unparsed("mixed operators")
                symbol = token.text
                left = Op(token.text, left, self.expr(prec + 1))
        self.depth -= 1
        return left

    def is_tail(self, left: Expr) -> Expr:
        words = ["IS"]
        if self.keyword() == "not":
            self.pos += 1
            words.append("NOT")
        token = self.take()
        word = token.text.upper()
        if token.kind != "id" or word not in (
            "NULL",
            "TRUE",
            "FALSE",
            "UNKNOWN",
            "DISTINCT",
        ):
            raise Unparsed("IS")
        words.append(word)
        if word == "DISTINCT":
            nxt = self.take()
            if nxt.kind != "id" or nxt.text.upper() != "FROM":
                raise Unparsed("IS DISTINCT")
            words.append("FROM")
            return Op(" ".join(words), left, self.unary())
        return Postfix(left, " ".join(words))

    def unary(self) -> Expr:
        if self.keyword() == "not":
            self.pos += 1
            return Not(self.expr(_NOT))
        return self.postfix(self.primary())

    def postfix(self, node: Expr) -> Expr:
        while (token := self.peek()) is not None and token.kind == "cast":
            self.pos += 1
            node = Cast(node, self.type_name())
        return node

    def primary(self) -> Expr:
        token = self.take()
        kind = token.kind
        if kind in ("str", "param", "num"):
            return Lit(token.text)
        if kind == "punct" and token.text == "(":
            return self.parenthesized()
        if kind in ("id", "qid"):
            return self.name(token)
        raise Unparsed(token.text)

    def parenthesized(self) -> Expr:
        head = self.peek()
        if head is not None and head.kind == "id" and head.text.lower() in _SUBPLAN:
            return self.subplan()
        inner = self.expr(0)
        if self.is_punct(","):
            items = [inner]
            while self.is_punct(","):
                self.pos += 1
                items.append(self.expr(0))
            self.expect(")")
            return Call("ROW", tuple(items))
        self.expect(")")
        return inner

    def subplan(self) -> Expr:
        """``(SubPlan 1)``, ``(hashed SubPlan 2)``, ``(InitPlan 1).col1``."""
        depth = 1
        while depth:
            token = self.take()
            if token.kind == "punct" and token.text == "(":
                depth += 1
            elif token.kind == "punct" and token.text == ")":
                depth -= 1
            elif token.kind not in ("id", "num"):
                raise Unparsed("sub-plan")
        if self.is_punct(".") and (self.peek(1) or _Token("", "")).kind == "id":
            self.pos += 2
        return Lit("subplan")

    def name(self, first: _Token) -> Expr:
        parts = [first.text]
        while self.is_punct(".") and (self.peek(1) or _Token("", "")).kind in (
            "id",
            "qid",
        ):
            self.pos += 1
            parts.append(self.take().text)
        word = first.text.lower() if first.kind == "id" else None
        if len(parts) == 1 and word == "array" and self.is_punct("["):
            self.pos += 1
            return Call("ARRAY", self.arguments("]"))
        if self.is_punct("("):
            self.pos += 1
            return Call(".".join(parts).lower(), self.arguments(")"))
        if len(parts) == 2:
            return Col(parts[0], parts[1])
        if len(parts) == 1 and first.kind == "id":
            return Lit(first.text)  # NULL, TRUE, CURRENT_DATE, ...
        raise Unparsed("name")

    def arguments(self, close: str) -> tuple[Expr, ...]:
        items: list[Expr] = []
        if self.is_punct(close):
            self.pos += 1
            return ()
        while True:
            items.append(self.expr(0))
            if self.is_punct(","):
                self.pos += 1
                continue
            self.expect(close)
            return tuple(items)

    def type_name(self) -> str:
        words: list[str] = []
        while True:
            token = self.peek()
            if token is None:
                break
            if token.kind == "id" and token.text.lower() not in _TYPE_STOP:
                words.append(token.text.lower())
                self.pos += 1
                while self.is_punct(".") and (self.peek(1) or _Token("", "")).kind in (
                    "id",
                    "qid",
                ):
                    words[-1] += "." + self.tokens[self.pos + 1].text.lower()
                    self.pos += 2
            elif token.kind == "qid" and not words:
                words.append(token.text)
                self.pos += 1
            elif token.kind == "punct" and token.text == "(" and words:
                words[-1] += self.type_modifier()
            else:
                break
        if not words:
            raise Unparsed("type")
        name = " ".join(words)
        while self.is_punct("[") and self.is_punct("]", 1):
            self.pos += 2
            name += "[]"
        return name

    def type_modifier(self) -> str:
        self.expect("(")
        parts: list[str] = []
        while not self.is_punct(")"):
            token = self.take()
            if token.kind not in ("num", "punct") or token.text in "()[]":
                raise Unparsed("type modifier")
            parts.append(token.text)
        self.expect(")")
        return "(" + "".join(parts) + ")"


@lru_cache(maxsize=512)
def parse(condition: str) -> Expr | None:
    """The tree of a plan condition, or None when it is outside the grammar."""
    try:
        return _Parser(_tokenize(condition)).parse()
    except Unparsed:
        return None
    except RecursionError:  # pragma: no cover - guarded by _MAX_DEPTH
        return None


def walk(
    node: Expr, parents: tuple[Expr, ...] = ()
) -> Iterator[tuple[Expr, tuple[Expr, ...]]]:
    """Every node with the chain of its ancestors, outermost first."""
    yield node, parents
    below = (*parents, node)
    children: tuple[Expr, ...]
    if isinstance(node, (Col, Lit)):
        return
    if isinstance(node, Call):
        children = node.args
    elif isinstance(node, Bool):
        children = node.items
    elif isinstance(node, Op):
        children = (node.left, node.right)
    else:
        children = (node.operand,)
    for child in children:
        yield from walk(child, below)


def unquote(name: str) -> str:
    if name.startswith('"'):
        return name[1:-1].replace('""', '"')
    return name
