"""Seeded, property-style tests for the text handling, standard library only.

Two kinds of input: generated statements and conditions that are valid in the
shapes the plugin sees (checked for invariants such as idempotence and
whitespace or literal independence) and random character soup (checked only
for "never raises, always returns the documented type, finishes quickly").
Every generator takes a fixed seed, so a failure reproduces exactly.
"""

from __future__ import annotations

import random
import time

import pytest

from pytest_queryguard.detectors import normalize
from pytest_queryguard.indexes import equality_column, hint_column

SEED = 20261012
ROUNDS = 400

SOUP = "abc XYZ'\"()?,$:\n\t\\-_=<>0123456789.INinANDor%;/*[]{}:text\x00é中"
SPACES = [" ", "  ", "\t", "\n", " \n\t ", "\r\n", " "[:0] or " "]


def _soup(rng: random.Random, limit: int = 60) -> str:
    return "".join(rng.choice(SOUP) for _ in range(rng.randint(0, limit)))


def _ws(rng: random.Random) -> str:
    return rng.choice(SPACES)


# ----------------------------------------------------------- normalize


def _number(rng: random.Random) -> str:
    whole = str(rng.randint(0, 10 ** rng.randint(1, 9)))
    return f"{whole}.{rng.randint(0, 999)}" if rng.random() < 0.3 else whole


def _string(rng: random.Random) -> str:
    chars = "ab c%_-:()=,\né"
    body = "".join(rng.choice(chars) for _ in range(rng.randint(0, 8)))
    if rng.random() < 0.3:
        body += "''" + rng.choice(chars)
    return f"'{body}'"


def _placeholder(rng: random.Random) -> str:
    return rng.choice(["?", "%s", "%(p_1)s", ":p_1", f"${rng.randint(1, 9)}"])


def _value(rng: random.Random) -> str:
    return rng.choice([_number, _string, _placeholder])(rng)


def _statement(rng: random.Random) -> list[str]:
    """A SELECT or write statement as a list of atomic tokens."""
    table = rng.choice(["orders", "customers", '"Order Items"', "public.t1"])
    tokens: list[str] = []
    verb = rng.choice(["select", "SELECT", "update", "delete", "insert"])
    if verb.lower() == "insert":
        tokens += [
            "INSERT",
            "INTO",
            table,
            "(a, b)",
            "VALUES",
            f"({_value(rng)},",
            f"{_value(rng)})",
        ]
        return tokens
    if verb.lower() == "update":
        tokens += ["UPDATE", table, "SET", "a", "=", _value(rng)]
    elif verb.lower() == "delete":
        tokens += ["DELETE", "FROM", table]
    else:
        tokens += [verb, rng.choice(["*", "t.a, t.b", "count(*)"]), "FROM", table]
    for i in range(rng.randint(0, 3)):
        tokens.append("WHERE" if i == 0 else rng.choice(["AND", "OR"]))
        col = rng.choice(["id", "a", '"B c"'])
        kind = rng.random()
        if kind < 0.5:
            tokens += [col, rng.choice(["=", "<", ">=", "<>"]), _value(rng)]
        elif kind < 0.8:
            items = ", ".join(_value(rng) for _ in range(rng.randint(1, 12)))
            tokens += [col, rng.choice(["IN", "in", "In"]), f"({items})"]
        else:
            tokens += [col, "LIKE", _string(rng)]
    if rng.random() < 0.3:
        tokens += ["LIMIT", _number(rng)]
    return tokens


def test_normalize_is_idempotent_on_generated_statements() -> None:
    rng = random.Random(SEED)
    for _ in range(ROUNDS):
        sql = " ".join(_statement(rng))
        once = normalize(sql)
        assert normalize(once) == once, sql


def test_normalize_ignores_whitespace_between_tokens() -> None:
    rng = random.Random(SEED + 1)
    for _ in range(ROUNDS):
        tokens = _statement(rng)
        plain = normalize(" ".join(tokens))
        spaced = normalize("".join(t + _ws(rng) for t in tokens))
        padded = normalize(_ws(rng) + " ".join(tokens) + _ws(rng))
        assert plain == spaced == padded, tokens


def test_normalize_ignores_the_values() -> None:
    # The same statement with other numbers, strings and IN-list lengths.
    rng = random.Random(SEED + 2)
    for _ in range(ROUNDS):
        table = rng.choice(["orders", "t1"])

        def build(rng: random.Random, table: str = table) -> str:
            items = ", ".join(_number(rng) for _ in range(rng.randint(1, 30)))
            return (
                f"SELECT * FROM {table} WHERE id = {_number(rng)} "
                f"AND name = {_string(rng)} AND k IN ({items}) LIMIT {_number(rng)}"
            )

        first = normalize(build(rng))
        for _ in range(3):
            assert normalize(build(rng)) == first


def test_normalize_keeps_different_statements_apart() -> None:
    # Different tables, columns or verbs must not collapse into one shape.
    shapes = {
        normalize(sql)
        for sql in (
            "SELECT * FROM a WHERE id = 1",
            "SELECT * FROM b WHERE id = 1",
            "SELECT * FROM a WHERE x = 1",
            "SELECT * FROM a WHERE id > 1",
            "DELETE FROM a WHERE id = 1",
            "SELECT * FROM a WHERE id = 1 AND x = 2",
        )
    }
    assert len(shapes) == 6


def test_normalize_survives_character_soup() -> None:
    rng = random.Random(SEED + 4)
    for _ in range(ROUNDS * 20):
        sql = _soup(rng)
        assert isinstance(normalize(sql), str)


# Short ids: a 100 kB parameter id would end up in PYTEST_CURRENT_TEST, which
# Windows rejects above 32767 characters.
ADVERSARIAL = {
    "empty": "",
    "space": " ",
    "quotes": "'" * 5000,
    "unterminated-string": "'" + "a" * 100_000,
    "open-in-list": "IN (" + "?, " * 20_000,
    "long-in-list": "IN (" + "?, " * 20_000 + "?)",
    "mismatched-parens": "IN (" + "1," * 5000 + "x)" * 5000,
    "open-parens": "(" * 5000,
    "many-numbers": "SELECT " + "1 " * 20_000,
    "newlines": "\n" * 50_000,
    "cast-words": "IN (?" + "::" + "a " * 3000 + ")",
    "cast-as-words": "IN (CAST(? AS " + "a " * 3000 + ")",
    "nul": "\x00" * 1000,
}


@pytest.mark.parametrize("name", list(ADVERSARIAL))
def test_normalize_is_fast_on_adversarial_input(name: str) -> None:
    sql = ADVERSARIAL[name]
    # A generous bound: this catches catastrophic regex backtracking, not speed.
    start = time.perf_counter()
    normalize(sql)
    assert time.perf_counter() - start < 3.0


def test_normalize_idempotence_gap_is_limited_to_glued_keywords() -> None:
    # "$7iN(9)" is not valid SQL, but it shows the one known way the second
    # pass differs: a placeholder glued to "IN" has no word boundary until the
    # placeholder becomes "?". Real statements separate them, so the shape is
    # stable for everything a driver sends.
    glued = "SELECT 1 WHERE $7iN(9)"
    assert normalize(normalize(glued)) != normalize(glued)
    assert normalize("SELECT 1 WHERE $7 iN (9)") == "SELECT ? WHERE ? IN (...)"


# ------------------------------------------------- plan condition parsing

OPS = ["=", "<", ">", "<=", ">="]


def _plan_literal(rng: random.Random) -> str:
    kind = rng.random()
    if kind < 0.3:
        return str(rng.randint(-5, 10**6))
    if kind < 0.6:
        # Literals that look like conditions must not confuse the parser.
        inner = rng.choice(
            ["a", "t.other = 1", "x) AND (t.b = 2", "it''s", "t.id", "(", ")", ""]
        )
        return f"'{inner}'::text"
    if kind < 0.8:
        return f"'{{{rng.randint(1, 9)},{rng.randint(1, 9)}}}'::integer[]"
    return f"${rng.randint(1, 9)}"


def _atom(rng: random.Random, alias: str, column: str) -> str:
    left = f"{alias}.{column}"
    kind = rng.random()
    if kind < 0.7:
        return f"({left} {rng.choice(OPS)} {_plan_literal(rng)})"
    if kind < 0.85:
        return f"({left} = ANY ({_plan_literal(rng)}))"
    return f"(({left})::text = {_plan_literal(rng)})"


def _condition(
    rng: random.Random, alias: str, columns: list[str]
) -> tuple[str, list[str]]:
    """(condition text, its atoms) where the atoms are joined by AND or OR."""
    atoms = [_atom(rng, alias, rng.choice(columns)) for _ in range(rng.randint(1, 4))]
    text = atoms[0]
    for atom in atoms[1:]:
        text = f"({text} {rng.choice(['AND', 'OR'])} {atom})"
    return text, atoms


def test_condition_parsing_survives_character_soup() -> None:
    rng = random.Random(SEED + 5)
    for _ in range(ROUNDS * 20):
        condition, alias = _soup(rng), _soup(rng, 8)
        varchar = {_soup(rng, 4) for _ in range(rng.randint(0, 3))}
        hint = hint_column(condition, alias, varchar)
        eq = equality_column(condition, alias, varchar)
        assert hint is None or isinstance(hint, str)
        assert eq is None or eq == hint


@pytest.mark.parametrize("alias", ["t", "orders", '"Order Items"', "a_1", 't"x', "é"])
def test_condition_result_names_a_column_of_the_condition(alias: str) -> None:
    rng = random.Random(SEED + 6)
    columns = ["id", "a", '"B c"', "customer_id"]
    for _ in range(ROUNDS):
        condition, _ = _condition(rng, alias, columns)
        hint = hint_column(condition, alias)
        eq = equality_column(condition, alias)
        if hint is not None:
            assert hint in columns
        assert eq is None or eq == hint


def test_condition_result_ignores_whitespace_runs() -> None:
    rng = random.Random(SEED + 7)
    columns = ["id", "a", "customer_id"]
    compared = 0
    for _ in range(ROUNDS):
        condition, _ = _condition(rng, "t", columns)
        # Literals are copied as they are, so only literal-free text is respaced.
        if "'" in condition:
            continue
        compared += 1
        spaced = condition.replace(" ", _ws(rng))
        assert hint_column(spaced, "t") == hint_column(condition, "t")
        assert equality_column(spaced, "t") == equality_column(condition, "t")
    assert compared > 50


def test_condition_result_ignores_literal_contents() -> None:
    # The same condition rendered with different quoted text: whatever the text
    # says (even "t.other = 1" or unbalanced parentheses), the answer is the same.
    junk = ["a", "t.other = 1", "x) AND (t.b = 2", "it''s", "t.id", "(", ")", ""]
    rng = random.Random(SEED + 8)
    for _ in range(ROUNDS):
        spec = [
            (rng.choice(["id", "a"]), rng.choice(OPS), rng.choice(["AND", "OR"]))
            for _ in range(rng.randint(1, 4))
        ]

        def render(texts: list[str], spec: list[tuple[str, str, str]] = spec) -> str:
            out = f"(t.{spec[0][0]} {spec[0][1]} '{texts[0]}'::text)"
            for (col, op, joiner), text in zip(spec[1:], texts[1:], strict=False):
                out = f"({out} {joiner} (t.{col} {op} '{text}'::text))"
            return out

        first = render([rng.choice(junk) for _ in spec])
        second = render([rng.choice(junk) for _ in spec])
        assert hint_column(first, "t") == hint_column(second, "t")
        assert equality_column(first, "t") == equality_column(second, "t")


def test_a_single_plain_comparison_is_found() -> None:
    rng = random.Random(SEED + 9)
    for _ in range(ROUNDS):
        column = rng.choice(["id", "a", "customer_id", '"B c"'])
        op = rng.choice(OPS)
        condition = f"(t.{column} {op} {_plan_literal(rng)})"
        assert hint_column(condition, "t") == column, condition
        expected = column if op == "=" else None
        assert equality_column(condition, "t") == expected, condition


def test_two_columns_never_give_a_hint() -> None:
    rng = random.Random(SEED + 10)
    for _ in range(ROUNDS):
        a, b = rng.sample(["id", "a", "b", "customer_id"], 2)
        joiner = rng.choice(["AND", "OR"])
        condition = (
            f"((t.{a} = {_plan_literal(rng)}) {joiner} (t.{b} = {_plan_literal(rng)}))"
        )
        assert hint_column(condition, "t") is None, condition
