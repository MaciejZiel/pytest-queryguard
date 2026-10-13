"""The parser for plan conditions, and its agreement with the text matching."""

from __future__ import annotations

import random

import pytest

from pytest_queryguard import advisor, indexes, predicates
from pytest_queryguard.predicates import (
    Bool,
    Call,
    Cast,
    Col,
    Lit,
    Not,
    Op,
    Postfix,
    parse,
)

VARCHAR = frozenset({"status", "name"})

CONDITIONS = [
    ("(orders.customer_id = $1)", "orders"),
    ("(orders.customer_id = ANY (ARRAY[$1, $2]))", "orders"),
    ("(orders.customer_id = ANY ($1))", "orders"),
    ("((orders.status)::text = $1)", "orders"),
    ("((orders.status)::text = ANY ('{a,b}'::text[]))", "orders"),
    ("(o.total < $1)", "o"),
    ("(c.id = o.customer_id)", "o"),
    ("((o.total > $1) AND (o.total < $2))", "o"),
    ('("Order".customer_id = $1)', "Order"),
    ('(o."customerId" = $1)', "o"),
    ("((o.name)::text = 'o.other'::text)", "o"),
    ("((o.a = $1) AND (o.b = $2))", "o"),
    ("((o.a = $1) OR ((o.status)::text = $2))", "o"),
    ("(lower((o.name)::text) = $1)", "o"),
    ("((o.total)::integer = $1)", "o"),
    ("((o.created_at)::date = $1)", "o"),
    ("((o.created)::timestamp without time zone >= $1)", "o"),
    ("($1 = (o.total)::integer)", "o"),
    ("((o.name)::text ~~ $1)", "o"),
    ("(o.status <> $1)", "o"),
    ("(o.status IS NULL)", "o"),
    ("(o.status IS NOT NULL)", "o"),
    ("($1 = ANY (o.tags))", "o"),
    ("((o.data ->> 'k'::text) = $1)", "o"),
    ("($1 = (o.data -> 'k'::text))", "o"),
    ("($1 IS NULL)", "o"),
    ("(c.id = $1)", "o"),
    ("(xo.id = $1)", "o"),
    ("(o.id = (SubPlan 1))", "o"),
    ("(o.id = (hashed SubPlan 2))", "o"),
    ("(o.customer_id = (InitPlan 1).col1)", "o"),
    ("(o.customer_id = $0)", "o"),
    ("((COALESCE(o.status, 'x'::character varying))::text = $1)", "o"),
    ("(o.id = ANY ('{1,2,3}'::integer[]))", "o"),
]


@pytest.mark.parametrize(("condition", "alias"), CONDITIONS)
def test_parsed_conditions_agree_with_the_text_matching(
    condition: str, alias: str
) -> None:
    assert parse(condition) is not None
    for varchar in (frozenset(), VARCHAR):
        assert indexes._comparisons(condition, alias, varchar) == (
            indexes._regex_comparisons(condition, alias, varchar)
        )
        assert indexes._needs_expression(condition, alias, varchar) == (
            indexes._regex_needs_expression(condition, alias, varchar)
        )
    assert indexes.condition_columns(condition, alias) == {
        indexes._unquote(m["col"]) for m in indexes._column_refs(condition, alias)
    }


def test_parse_builds_a_tree_of_columns_casts_and_operators() -> None:
    tree = parse("(((o.status)::text = $1) AND (o.total > '5'::numeric))")
    assert tree == Bool(
        "AND",
        (
            Op("=", Cast(Col("o", "status"), "text"), Lit("$1")),
            Op(">", Col("o", "total"), Cast(Lit("'5'"), "numeric")),
        ),
    )


def test_parse_flattens_chains_and_keeps_the_nesting_of_parentheses() -> None:
    flat = parse("((a.x = $1) AND (a.y = $2) AND (a.z = $3))")
    assert isinstance(flat, Bool) and len(flat.items) == 3
    nested = parse("((a.x = $1) AND ((a.y = $2) OR (a.z = $3)))")
    assert isinstance(nested, Bool) and isinstance(nested.items[1], Bool)
    assert nested.items[1].op == "OR"


def test_parse_reads_postfix_not_and_calls() -> None:
    assert parse("(a.x IS NOT NULL)") == Postfix(Col("a", "x"), "IS NOT NULL")
    assert parse("(NOT (a.x = $1))") == Not(Op("=", Col("a", "x"), Lit("$1")))
    assert parse("(a.x IS DISTINCT FROM $1)") == Op(
        "IS DISTINCT FROM", Col("a", "x"), Lit("$1")
    )
    assert parse("(lower((a.x)::text) = $1)") == Op(
        "=", Call("lower", (Cast(Col("a", "x"), "text"),)), Lit("$1")
    )
    assert parse("(a.x = ANY (ARRAY[$1, $2]))") == Op(
        "=", Col("a", "x"), Call("any", (Call("ARRAY", (Lit("$1"), Lit("$2"))),))
    )


@pytest.mark.parametrize(
    ("text", "tree"),
    [
        (
            "(CASE WHEN (a.x = 'n'::text) THEN 1 ELSE 0 END = $1)",
            Op(
                "=",
                Call(
                    "case",
                    (
                        Op("=", Col("a", "x"), Cast(Lit("'n'"), "text")),
                        Lit("1"),
                        Lit("0"),
                    ),
                ),
                Lit("$1"),
            ),
        ),
        (
            "CASE a.x WHEN 1 THEN true WHEN 2 THEN false END",
            Call(
                "case",
                (Col("a", "x"), Lit("1"), Lit("true"), Lit("2"), Lit("false")),
            ),
        ),
        (
            "((a.t AT TIME ZONE 'UTC'::text) > $1)",
            Op(
                ">",
                Call("timezone", (Cast(Lit("'UTC'"), "text"), Col("a", "t"))),
                Lit("$1"),
            ),
        ),
        ("(a.t AT LOCAL)", Call("timezone", (Col("a", "t"),))),
        (
            "(EXTRACT(year FROM a.t) = $1)",
            Op("=", Call("extract", (Lit("year"), Col("a", "t"))), Lit("$1")),
        ),
        (
            "(SUBSTRING(a.x FROM 1 FOR 2) = $1)",
            Op(
                "=",
                Call("substring", (Col("a", "x"), Lit("1"), Lit("2"))),
                Lit("$1"),
            ),
        ),
        (
            "(TRIM(BOTH ' '::text FROM a.x) = $1)",
            Op(
                "=",
                Call("trim", (Cast(Lit("' '"), "text"), Col("a", "x"))),
                Lit("$1"),
            ),
        ),
        ("(count(*) > $1)", Op(">", Call("count", (Lit("*"),)), Lit("$1"))),
        (
            "(count(DISTINCT a.x) > 1)",
            Op(">", Call("count", (Col("a", "x"),)), Lit("1")),
        ),
        (
            "((a.tags)[1] = $1)",
            Op("=", Call("subscript", (Col("a", "tags"), Lit("1"))), Lit("$1")),
        ),
        (
            '((a.x)::text = ($1)::text COLLATE "C")',
            Op(
                "=",
                Cast(Col("a", "x"), "text"),
                Call("collate", (Cast(Lit("$1"), "text"),)),
            ),
        ),
        (
            '(a.x COLLATE pg_catalog."default" = $1)',
            Op("=", Call("collate", (Col("a", "x"),)), Lit("$1")),
        ),
        (
            "(ROW(a.x, a.y) > ROW($1, $2))",
            Op(
                ">",
                Call("row", (Col("a", "x"), Col("a", "y"))),
                Call("row", (Lit("$1"), Lit("$2"))),
            ),
        ),
    ],
)
def test_parse_reads_case_time_zones_keyword_arguments_and_subscripts(
    text: str, tree: object
) -> None:
    assert parse(text) == tree


# Condition texts from real plans (tests/fixtures/plans) whose meaning the text
# matching gets wrong; the parsed tree decides for them.
@pytest.mark.parametrize(
    ("condition", "alias", "column", "expression"),
    [
        # #83: NOT IN (subquery) as PostgreSQL 17 and 18 print it
        (
            "(NOT (ANY (customers.id = (hashed SubPlan 1).col1)))",
            "customers",
            None,
            False,
        ),
        ("(NOT (o.id = $1))", "o", None, False),
        # #84: CASE, AT TIME ZONE and EXTRACT hide the column from an index
        (
            "(CASE WHEN ((orders.status)::text = 'new'::text) THEN 1 ELSE 0 END = $1)",
            "orders",
            None,
            True,
        ),
        (
            "((orders.created_at AT TIME ZONE 'UTC'::text) > $1)",
            "orders",
            None,
            True,
        ),
        ("(EXTRACT(year FROM orders.created_at) = $1)", "orders", None, True),
        ("((orders.tags)[1] = $1)", "orders", None, True),
        # #85: a btree on (customer_id, total) serves a row comparison
        (
            "(ROW(orders.customer_id, orders.total) > ROW($1, $2))",
            "orders",
            None,
            False,
        ),
    ],
)
def test_the_parsed_tree_decides_where_the_text_matching_is_wrong(
    condition: str, alias: str, column: str | None, expression: bool
) -> None:
    assert parse(condition) is not None
    assert indexes.hint_column(condition, alias, VARCHAR) == column
    assert indexes.equality_column(condition, alias, VARCHAR) == column
    assert indexes._needs_expression(condition, alias, VARCHAR) is expression


def test_syntax_calls_are_not_rendered_into_an_index_definition() -> None:
    for text in (
        "CASE WHEN (a.x = 1) THEN 1 ELSE 0 END",
        "(a.t AT TIME ZONE 'UTC'::text)",
        "EXTRACT(year FROM a.t)",
        "(a.tags)[1]",
    ):
        tree = parse(text)
        assert tree is not None
        with pytest.raises(advisor._Unrenderable):
            advisor.render(tree, "a")


@pytest.mark.parametrize(
    ("text", "type_name"),
    [
        ("(a.x)::text", "text"),
        ("(a.x)::character varying", "character varying"),
        ("(a.x)::character varying(10)", "character varying(10)"),
        ("(a.x)::numeric(10,2)", "numeric(10,2)"),
        ("(a.x)::timestamp without time zone", "timestamp without time zone"),
        ("(a.x)::timestamp(3) with time zone", "timestamp(3) with time zone"),
        ("(a.x)::integer[]", "integer[]"),
        ("(a.x)::public.mood", "public.mood"),
        ('(a.x)::"Mood"', '"Mood"'),
        ("(a.x)::text::integer", "integer"),
    ],
)
def test_cast_type_names(text: str, type_name: str) -> None:
    tree = parse(text)
    assert isinstance(tree, Cast) and tree.type_name == type_name


def test_string_literals_hide_their_content() -> None:
    tree = parse("(a.x = 'b.y AND (c.z = 1)'::text)")
    assert tree == Op("=", Col("a", "x"), Cast(Lit("'b.y AND (c.z = 1)'"), "text"))
    assert parse("(a.x = 'it''s')") == Op("=", Col("a", "x"), Lit("'it''s'"))


@pytest.mark.parametrize(
    "text",
    [
        "",
        "(",
        "a.x = ",
        "(a.x = $1",
        "(a.x = $1))",
        "a.x $1",
        "(a.x = $1) AND",
        "CASE WHEN a.x THEN 1",  # no END
        "CASE a.x END",  # no WHEN
        "CASE WHEN a.x 1 END",  # no THEN
        "(a.x = 1 + 2 * 3)",  # two operators without parentheses
        "(alternatives: SubPlan 1 or hashed SubPlan 2)",
        "(a.x)::",
        "E'a\\nb'",
        "(a.x COLLATE)",
        "(a.x COLLATE 1)",
        "(a.x AT TIME $1)",
        "a.b.c.d",
        "((a.x)[1:2] = $1)",
        "(a.x IS DOCUMENT)",
        "(count(*) FILTER (WHERE (a.x = 1)) > 1)",
        "(a.x = ¬)",
    ],
)
def test_unknown_syntax_is_not_parsed(text: str) -> None:
    assert parse(text) is None


def test_deep_nesting_is_not_parsed_instead_of_overflowing() -> None:
    assert parse("(" * 500 + "a.x = $1" + ")" * 500) is None
    assert parse("(" * 20 + "a.x = $1" + ")" * 20) is not None


def test_fallback_to_text_matching_for_unparsed_conditions() -> None:
    # an array slice is outside the grammar: the text matching still answers
    condition = "(((o.x)[1:2] = $1) OR (o.y = $2))"
    assert parse(condition) is None
    assert indexes.hint_column("(o.total < $1)", "o") == "total"
    assert indexes.hint_column(condition, "o") is None
    assert indexes.condition_columns(condition, "o") == {"x", "y"}


def test_unquote() -> None:
    assert predicates.unquote('"a""b"') == 'a"b'
    assert predicates.unquote("abc") == "abc"


def test_walk_yields_ancestors_outermost_first() -> None:
    tree = parse("((a.x)::text = $1)")
    assert tree is not None
    nodes = [(type(n).__name__, len(parents)) for n, parents in predicates.walk(tree)]
    assert nodes == [("Op", 0), ("Cast", 1), ("Col", 2), ("Lit", 1)]


# Random input must never raise, hang or return a tree for broken text.

_PIECES = [
    "(", ")", "a.x", "b.y", '"Q".c', "$1", "$2", "=", "<", ">=", "<>", "->>", "~~",
    "AND", "OR", "NOT", "IS", "NULL", "ANY", "ARRAY[", "]", ",", "::text", "::int",
    "'s'", "'a''b'", "1", "2.5", "lower(", "COALESCE(", "(SubPlan 1)", ".", " ", "::",
    "CASE", "WHEN", "THEN", "ELSE", "END", "AT", "TIME", "ZONE", "LOCAL", "COLLATE",
    '"C"', "[", "EXTRACT(", "FROM", "FOR", "IN", "DISTINCT", "BOTH", "*", "count(*)",
    "ROW(",
]  # fmt: skip


def test_random_token_soup_never_raises() -> None:
    rng = random.Random(20261012)
    for _ in range(3000):
        text = " ".join(rng.choice(_PIECES) for _ in range(rng.randint(0, 14)))
        parse.__wrapped__(text)  # bypass the cache: every input is parsed afresh


# Real condition texts with the syntax added for #84 and #85, for mutation.
_NEW_SYNTAX = [
    ("(CASE WHEN ((orders.status)::text = 'new'::text) THEN 1 ELSE 0 END = $1)", "orders"),
    ("((orders.created_at AT TIME ZONE 'UTC'::text) > $1)", "orders"),
    ("(EXTRACT(year FROM orders.created_at) = $1)", "orders"),
    ("(count(*) > $1)", "orders"),
    ("(ROW(orders.customer_id, orders.total) > ROW($1, $2))", "orders"),
    ("(NOT (ANY (customers.id = (hashed SubPlan 1).col1)))", "customers"),
    ('((o.name)::text = ($1)::text COLLATE "C")', "o"),
    ("((o.tags)[1] = $1)", "o"),
]  # fmt: skip


def test_mutated_real_conditions_never_raise() -> None:
    rng = random.Random(7)
    alphabet = "()[]'\",.:$ =<>-+*aZ09"
    for condition, alias in [*CONDITIONS, *_NEW_SYNTAX]:
        for _ in range(60):
            chars = list(condition)
            for _ in range(rng.randint(1, 4)):
                position = rng.randrange(len(chars) + 1)
                if chars and rng.random() < 0.5 and position < len(chars):
                    del chars[position]
                else:
                    chars.insert(position, rng.choice(alphabet))
            text = "".join(chars)
            parse.__wrapped__(text)
            # and the callers behave the same whatever the parser made of it
            indexes._comparisons(text, alias, VARCHAR)
            indexes._needs_expression(text, alias, VARCHAR)
            indexes.condition_columns(text, alias)
