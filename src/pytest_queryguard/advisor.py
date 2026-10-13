"""Index advisor: a concrete CREATE INDEX for a missing-index finding.

The suggestion is built from the conditions of the plan node that reads the
table (parsed by predicates.py), the sort keys of a Sort directly above it and
the indexes the table already has. hypopg, when present, then checks it (see
hypopg.py). See docs/index-advisor.md.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import NamedTuple

from . import predicates
from .events import Advice, IndexKey, ScanIssue
from .predicates import Bool, Cast, Col, Expr, Lit, Not, Op, Postfix

# An index with more key columns than this costs more to keep than it saves;
# the equality columns come first, so the last ones are the ones left out.
MAX_KEY_COLUMNS = 4
# A partial index is suggested over the full one only when hypopg estimates
# it at most this fraction of the full index's size.
PARTIAL_SIZE_RATIO = 0.25

_RANGE_OPS = frozenset({"<", "<=", ">", ">="})
_SORT_KEY = re.compile(
    r"(?P<ref>.+?)(?P<suffix>(?: ASC| DESC)?(?: NULLS (?:FIRST|LAST))?)", re.DOTALL
)


class ExistingIndex(NamedTuple):
    """An index the table already has, as the catalog describes it."""

    name: str
    method: str  # "btree", "hash", "gin", ...
    valid: bool
    columns: tuple[str | None, ...]  # key columns; None for an expression
    predicate: str | None  # the WHERE of a partial index


@dataclass(frozen=True, slots=True)
class Candidate:
    """The key columns of one suggested index and its partial predicate."""

    keys: tuple[IndexKey, ...]
    where: str | None = None


@dataclass(frozen=True, slots=True)
class Analysis:
    """What the conditions of a scan say about the index that would serve it."""

    plain: Candidate | None
    partial: Candidate | None  # a constant predicate moved into WHERE


class _Unrenderable(Exception):
    pass


def _flatten(expr: Expr) -> list[Expr]:
    if isinstance(expr, Bool) and expr.op == "AND":
        return [leaf for item in expr.items for leaf in _flatten(item)]
    return [expr]


def _mentions(expr: Expr, alias: str) -> bool:
    return any(
        isinstance(node, Col) and predicates.unquote(node.qualifier) == alias
        for node, _ in predicates.walk(expr)
    )


def _plain(expr: Expr, alias: str, varchar: Collection[str]) -> Col | None:
    """The column of ``alias`` when ``expr`` is just that column, or a varchar one
    cast to text (which an index on the plain column still serves)."""
    node = expr
    if isinstance(node, Cast):
        if node.type_name != "text":
            return None
        node = node.operand
        if not (isinstance(node, Col) and predicates.unquote(node.name) in varchar):
            return None
    if isinstance(node, Col) and predicates.unquote(node.qualifier) == alias:
        return node
    return None


def render(expr: Expr, alias: str) -> str:
    """The expression as SQL for an index definition, without ``alias.``."""
    if isinstance(expr, Col):
        if predicates.unquote(expr.qualifier) == alias:
            return expr.name
        return f"{expr.qualifier}.{expr.name}"
    if isinstance(expr, Lit):
        if expr.text == "subplan" or expr.text.startswith("$"):
            raise _Unrenderable
        return expr.text
    if isinstance(expr, Cast):
        inner = render(expr.operand, alias)
        if isinstance(expr.operand, Lit):
            return f"{inner}::{expr.type_name}"
        return f"({inner})::{expr.type_name}"
    if isinstance(expr, Op):
        return f"({render(expr.left, alias)} {expr.op} {render(expr.right, alias)})"
    if isinstance(expr, Postfix):
        return f"({render(expr.operand, alias)} {expr.text})"
    if isinstance(expr, Not):
        return f"(NOT {render(expr.operand, alias)})"
    if isinstance(expr, Bool):
        return "(" + f" {expr.op} ".join(render(i, alias) for i in expr.items) + ")"
    if expr.name in predicates.SYNTAX_CALLS:
        raise _Unrenderable  # CASE, AT TIME ZONE, ... are not printed back
    args = ", ".join(render(a, alias) for a in expr.args)
    return f"{expr.name}({args})" if expr.name != "ARRAY" else f"ARRAY[{args}]"


def _top(text: str) -> str:
    """``text`` without one pair of parentheses around all of it."""
    if not (text.startswith("(") and text.endswith(")")):
        return text
    depth = 0
    for i, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0 and i < len(text) - 1:
                return text
    return text[1:-1]


def _sort_columns(sort_keys: Sequence[str], alias: str) -> tuple[IndexKey, ...]:
    """The Sort node's keys as index keys, or none when one is not a plain column."""
    keys: list[IndexKey] = []
    for text in sort_keys:
        match = _SORT_KEY.fullmatch(text)
        tree = predicates.parse(match["ref"]) if match else None
        if (
            match is None
            or not isinstance(tree, Col)
            or predicates.unquote(tree.qualifier) != alias
        ):
            return ()
        keys.append(IndexKey(tree.name, match["suffix"].replace(" ASC", "")))
    return tuple(keys)


def analyse(issue: ScanIssue, varchar: Collection[str]) -> Analysis | None:
    """The index that would serve ``issue``, or None when its conditions give no
    plain column to index (the existing hint then stands)."""
    alias = issue.alias or issue.table
    trees = [predicates.parse(text) for text in issue.conditions]
    if not trees or any(tree is None for tree in trees):
        return None
    equal: list[Col] = []
    ranges: list[Col] = []
    constants: list[tuple[Col | None, Expr]] = []  # (key column, predicate)
    for tree in trees:
        assert tree is not None
        for leaf in _flatten(tree):
            _classify(leaf, alias, varchar, equal, ranges, constants)
    names = list(dict.fromkeys(c.name for c in equal))
    range_names = [n for n in dict.fromkeys(c.name for c in ranges) if n not in names]
    if not names and not range_names:
        return None
    sort = _sort_columns(issue.sort_keys, alias)

    def build(skip: set[str]) -> Candidate | None:
        eq = [n for n in names if n not in skip]
        keys = [IndexKey(n) for n in eq]
        rng = [n for n in range_names if n not in skip]
        if sort and not rng:
            keys += [k for k in sort if k.column not in eq]
        elif sort and issue.limit:
            # a LIMIT over the sort: reading the index in order stops early,
            # the range is then applied to the rows it returns
            keys += [k for k in sort if k.column not in eq]
        elif rng:
            keys.append(IndexKey(rng[0]))
        keys = keys[:MAX_KEY_COLUMNS]
        if not keys:
            return None
        return Candidate(tuple(keys))

    plain = build(set())
    partial: Candidate | None = None
    renderable: list[str] = []
    skip: set[str] = set()
    for column, leaf in constants:
        try:
            renderable.append(_top(render(leaf, alias)))
        except _Unrenderable:
            continue
        if column is not None:
            skip.add(column.name)
    if renderable:
        inner = build(skip)
        if inner is not None:
            partial = Candidate(inner.keys, " AND ".join(renderable))
    return Analysis(plain, partial)


def _classify(
    leaf: Expr,
    alias: str,
    varchar: Collection[str],
    equal: list[Col],
    ranges: list[Col],
    constants: list[tuple[Col | None, Expr]],
) -> None:
    if isinstance(leaf, Postfix) and leaf.text in ("IS NULL", "IS NOT NULL"):
        column = _plain(leaf.operand, alias, varchar)
        if column is not None:
            if leaf.text == "IS NULL":
                equal.append(column)
                constants.append((column, leaf))
            else:
                constants.append((None, leaf))
        return
    if not isinstance(leaf, Op) or not (leaf.op == "=" or leaf.op in _RANGE_OPS):
        return
    for column_side, other in ((leaf.left, leaf.right), (leaf.right, leaf.left)):
        column = _plain(column_side, alias, varchar)
        if column is None or _mentions(other, alias):
            continue
        (equal if leaf.op == "=" else ranges).append(column)
        if leaf.op == "=" and _is_constant(other):
            constants.append((column, leaf))
        return


def _is_constant(expr: Expr) -> bool:
    """A literal in the statement itself, not a bound parameter."""
    node = expr.operand if isinstance(expr, Cast) else expr
    return (
        isinstance(node, Lit)
        and not node.text.startswith("$")
        and node.text != ("subplan")
    )


# ------------------------------------------------------------------ existing


def blocking_index(
    keys: Sequence[IndexKey], existing: Sequence[ExistingIndex]
) -> str | None:
    """Why no new index is suggested, when the table already has one for these
    columns that the plan did not use; None when nothing like that exists."""
    wanted = tuple(predicates.unquote(k.column) for k in keys)
    unused = partial = invalid = None
    for index in existing:
        if index.method not in ("btree", "hash") or index.columns[: len(wanted)] != (
            wanted
        ):
            continue
        if index.method == "hash" and len(wanted) > 1:
            continue
        columns = ", ".join(c or "<expression>" for c in index.columns)
        where = f"{index.name} on ({columns})"
        if not index.valid:
            invalid = invalid or (
                f"index {where} exists but is marked invalid (a CREATE INDEX "
                "CONCURRENTLY that failed?): drop it and create it again"
            )
        elif index.predicate is not None:
            partial = partial or (
                f"index {where} is partial (WHERE {index.predicate}) and serves "
                "only statements that include that condition: add it to the "
                "query, or create a full index if the query must not have it"
            )
        else:
            unused = unused or (
                f"index {where} exists and covers these columns, so the plan "
                "does not need a new one: compare the types of the column and "
                "the parameter (an implicit cast), the collation and the "
                "operator class"
            )
    return unused or invalid or partial


# ----------------------------------------------------------------- rendering

_IDENT_UNSAFE = re.compile(r"\W+")


def index_name(table: str, keys: Sequence[IndexKey]) -> str:
    """``ix_<table>_<columns>``, shortened to PostgreSQL's 63 bytes."""
    parts = [table, *(predicates.unquote(k.column) for k in keys)]
    name = "ix_" + _IDENT_UNSAFE.sub("_", "_".join(parts)).strip("_")
    if len(name.encode()) <= 63:
        return name
    digest = hashlib.sha1(name.encode(), usedforsecurity=False).hexdigest()[:8]
    return name.encode()[:54].decode(errors="ignore") + "_" + digest


def create_sql(table_label: str, candidate: Candidate) -> str:
    columns = ", ".join(k.column + k.suffix for k in candidate.keys)
    sql = f"CREATE INDEX ON {table_label} ({columns})"
    if candidate.where:
        sql += f" WHERE {candidate.where}"
    return sql


def alembic_snippet(issue: ScanIssue, candidate: Candidate) -> str:
    """The ``op.create_index`` call for a migration."""
    columns = []
    for key in candidate.keys:
        if key.suffix:
            text = predicates.unquote(key.column) + key.suffix
            columns.append(f"sa.text({json.dumps(text)})")
        else:
            columns.append(json.dumps(predicates.unquote(key.column)))
    arguments = [
        json.dumps(index_name(issue.table, candidate.keys)),
        json.dumps(issue.table),
        "[" + ", ".join(columns) + "]",
    ]
    if issue.schema is not None and issue.show_schema:
        arguments.append(f"schema={json.dumps(issue.schema)}")
    if candidate.where:
        arguments.append(f"postgresql_where=sa.text({json.dumps(candidate.where)})")
    one_line = f"op.create_index({', '.join(arguments)})"
    if len(one_line) <= 88:
        return one_line
    body = "".join(f"    {argument},\n" for argument in arguments)
    return f"op.create_index(\n{body})"


def make_advice(
    issue: ScanIssue,
    candidate: Candidate,
    table_label: str,
    existing: Sequence[ExistingIndex],
) -> Advice:
    """The advice for one candidate: a CREATE INDEX, or why there is none."""
    blocked = None if candidate.where else blocking_index(candidate.keys, existing)
    if blocked is not None:
        return Advice(keys=candidate.keys, skipped=blocked)
    return Advice(
        keys=candidate.keys,
        where=candidate.where,
        create_sql=create_sql(table_label, candidate),
        alembic=alembic_snippet(issue, candidate),
    )
