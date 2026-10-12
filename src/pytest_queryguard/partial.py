"""Statements whose plan a partial index may change.

The index check explains a statement with a *generic* plan: bound values are
parameters (``$1``), so the planner cannot prove that a partial index such as
``... (x) WHERE status = 'open'`` serves ``status = $1 AND x = $2``. A real
execution, which plans with the bound values, may well use it. When the
statement compares a column of such an index's predicate with a parameter, the
generic plan cannot decide, and the check reports nothing instead of a missing
index that may not be missing. See docs/design-0.2.md, section 4.6.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Sequence
from typing import NamedTuple

from . import predicates

_LITERAL = re.compile(r"'(?:[^']|'')*'")
_CAST = re.compile(r"::\s*[\w\"$.]+")
_IDENTIFIER = re.compile(r'"(?:[^"]|"")+"|[^\W\d][\w$]*')
_COMPARISONS = frozenset({"=", "<", "<=", ">", ">=", "<>", "!="})


class PartialIndex(NamedTuple):
    """A valid partial index, as the catalog describes it."""

    schema: str
    table: str
    name: str
    predicate: str
    table_columns: frozenset[str]


def predicate_columns(predicate: str, table_columns: Collection[str]) -> set[str]:
    """The columns of the table that the index predicate mentions."""
    text = _CAST.sub(" ", _LITERAL.sub("''", predicate))
    return {
        name
        for token in _IDENTIFIER.findall(text)
        if (name := predicates.unquote(token)) in table_columns
    }


def _columns_under(node: predicates.Expr, alias: str) -> set[str]:
    return {
        predicates.unquote(found.name)
        for found, _ in predicates.walk(node)
        if isinstance(found, predicates.Col)
        and predicates.unquote(found.qualifier) == alias
    }


def _has_parameter(node: predicates.Expr) -> bool:
    return any(
        isinstance(found, predicates.Lit) and found.text.startswith("$")
        for found, _ in predicates.walk(node)
    )


def parameter_columns(condition: str, alias: str) -> set[str] | None:
    """Columns of ``alias`` that the condition compares with a parameter.

    None when the condition cannot be parsed.
    """
    tree = predicates.parse(condition)
    if tree is None:
        return None
    found: set[str] = set()
    for node, _ in predicates.walk(tree):
        if isinstance(node, predicates.Op) and node.op in _COMPARISONS:
            if _has_parameter(node):
                found |= _columns_under(node, alias)
    return found


def condition_columns(condition: str, alias: str) -> set[str] | None:
    tree = predicates.parse(condition)
    return None if tree is None else _columns_under(tree, alias)


def may_serve(
    indexes: Iterable[PartialIndex], conditions: Sequence[str], alias: str
) -> list[str]:
    """Names of the partial indexes the generic plan cannot rule out.

    An index qualifies when every column of its predicate is filtered by the
    statement and at least one of them is compared with a parameter: a
    predicate that the statement fixes with a literal (``deleted_at IS NULL``)
    is decided by the generic plan itself. Conditions that cannot be parsed
    never qualify, so the finding is kept.
    """
    filtered: set[str] = set()
    parameters: set[str] = set()
    for condition in conditions:
        columns = condition_columns(condition, alias)
        compared = parameter_columns(condition, alias)
        if columns is None or compared is None:
            return []
        filtered |= columns
        parameters |= compared
    names = []
    for index in indexes:
        columns = predicate_columns(index.predicate, index.table_columns)
        if columns and columns <= filtered and columns & parameters:
            names.append(index.name)
    return names
