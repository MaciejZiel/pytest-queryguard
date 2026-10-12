"""The hypopg driver and the advisor flow with a scripted cursor (no database)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import pytest

from pytest_queryguard import hypopg, indexes
from pytest_queryguard.events import Catalog, ScanIssue, Verification
from pytest_queryguard.indexes import AdvisorOptions

PLAN: Mapping[str, Any] = {"Node Type": "Seq Scan", "Total Cost": 100.0}
HYPO = "<1234>btree_orders_customer_id"


def hypothetical(cost: float) -> Mapping[str, Any]:
    return {
        "Node Type": "Nested Loop",
        "Total Cost": cost,
        "Plans": [{"Node Type": "Index Scan", "Index Name": HYPO}],
    }


class Script:
    """A cursor that records statements and answers some of them."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.statements: list[str] = []
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.rows: list[Any] = []
        self.fail_on = fail_on
        self.hypothetical = False

    def execute(self, sql: str, *params: Any) -> None:
        self.statements.append(sql)
        self.calls.append((sql, params))
        if self.fail_on and self.fail_on in sql:
            self.fail_on = None
            raise RuntimeError(f"failed: {sql[:30]}")
        self.rows = []
        if "extnamespace" in sql:
            self.rows = [("public",)]
        elif "hypopg_create_index" in sql:
            self.hypothetical = True
            self.rows = [(HYPO, 8192)]
        elif "hypopg_reset" in sql:
            self.hypothetical = False

    def fetchone(self) -> Any:
        return self.rows[0] if self.rows else None

    def close(self) -> None:
        pass


def plan_with_for(script: Script) -> Callable[[bool], Mapping[str, Any]]:
    def plan_with(seqscan: bool) -> Mapping[str, Any]:
        script.statements.append(f"PLAN seqscan={seqscan}")
        if script.hypothetical:
            return hypothetical(10.0 if seqscan else 9999.0)
        return PLAN

    return plan_with


def test_verify_runs_in_a_nested_savepoint_and_resets_before_releasing() -> None:
    script = Script()
    [result] = hypopg.verify(
        script, plan_with_for(script), ["CREATE INDEX ON orders (customer_id)"],
        asyncpg=False,
    )  # fmt: skip
    assert result == Verification(
        used=True, cost_before=100.0, cost_after=10.0, scan="Index Scan", size=8192
    )
    assert script.statements == [
        "SAVEPOINT queryguard_hypopg",
        hypopg._SCHEMA,
        "PLAN seqscan=True",
        (
            "SELECT indexname, public.hypopg_relation_size(indexrelid) "
            "FROM public.hypopg_create_index('CREATE INDEX ON orders (customer_id)')"
        ),
        "PLAN seqscan=False",
        "PLAN seqscan=True",
        "SELECT public.hypopg_reset()",
        "SELECT public.hypopg_reset()",
        "RELEASE SAVEPOINT queryguard_hypopg",
    ]
    assert not script.hypothetical


def test_verify_on_asyncpg_passes_the_definition_as_a_parameter() -> None:
    script = Script()
    hypopg.verify(
        script, plan_with_for(script), ["CREATE INDEX ON o (a) WHERE x = 'y'"],
        asyncpg=True,
    )  # fmt: skip
    create = [s for s in script.statements if "hypopg_create_index" in s]
    assert create == [
        (
            "SELECT indexname, public.hypopg_relation_size(indexrelid) "
            "FROM public.hypopg_create_index($1)"
        )
    ]
    assert (create[0], ("CREATE INDEX ON o (a) WHERE x = 'y'",)) in script.calls


def test_verify_says_when_the_planner_does_not_use_the_index() -> None:
    script = Script()

    def never(seqscan: bool) -> Mapping[str, Any]:
        return PLAN

    [result] = hypopg.verify(script, never, ["CREATE INDEX ON o (a)"], asyncpg=False)
    assert result.used is False and result.scan is None
    assert result.cost_before == result.cost_after == 100.0


def test_verify_creates_the_extension_first_when_asked() -> None:
    script = Script()
    hypopg.verify(
        script, plan_with_for(script), ["CREATE INDEX ON o (a)"],
        asyncpg=False, create_extension=True,
    )  # fmt: skip
    assert script.statements[:3] == [
        "SAVEPOINT queryguard_hypopg",
        "CREATE EXTENSION IF NOT EXISTS hypopg",
        hypopg._SCHEMA,
    ]


def test_verify_recovers_from_a_failed_definition_and_goes_on() -> None:
    script = Script(fail_on="bad_table")
    results = hypopg.verify(
        script,
        plan_with_for(script),
        ["CREATE INDEX ON bad_table (a)", "CREATE INDEX ON orders (a)"],
        asyncpg=False,
    )
    assert results[0].error == "failed: SELECT indexname, public.hypop"
    assert results[1].used is True
    failed = next(i for i, s in enumerate(script.statements) if "bad_table" in s)
    assert script.statements[failed + 1 : failed + 3] == [
        "ROLLBACK TO SAVEPOINT queryguard_hypopg",
        "SELECT public.hypopg_reset()",
    ]
    assert script.statements[-2:] == [
        "SELECT public.hypopg_reset()",
        "RELEASE SAVEPOINT queryguard_hypopg",
    ]


def test_verify_marks_all_results_when_the_baseline_plan_fails() -> None:
    script = Script()

    def broken(seqscan: bool) -> Mapping[str, Any]:
        raise ValueError("unreadable plan")

    results = hypopg.verify(script, broken, ["a", "b"], asyncpg=False)
    assert [r.error for r in results] == ["unreadable plan"] * 2
    assert script.statements[-2:] == [
        "SELECT public.hypopg_reset()",
        "RELEASE SAVEPOINT queryguard_hypopg",
    ]


def test_verify_reads_the_message_of_a_pg8000_error() -> None:
    script = Script()

    def broken(seqscan: bool) -> Mapping[str, Any]:
        raise RuntimeError({"M": "relation does not exist", "C": "42P01"})

    [result] = hypopg.verify(script, broken, ["a"], asyncpg=False)
    assert result.error == "relation does not exist"


def test_a_definition_with_a_backslash_is_not_sent() -> None:
    script = Script()
    [result] = hypopg.verify(
        script, plan_with_for(script), ["CREATE INDEX ON o (a) WHERE x = E'\\\\'"],
        asyncpg=False,
    )  # fmt: skip
    assert result.error == "the definition cannot be quoted"
    assert not any("hypopg_create_index" in s for s in script.statements)


def test_literal_quotes_and_refuses() -> None:
    assert hypopg.literal("it's") == "'it''s'"
    assert hypopg.literal("a\\b") is None
    assert hypopg.literal("a\x00b") is None


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (("1.4.3", "1.4.3"), ("1.4.3", "1.4.3")),
        ((None, "1.4.3"), (None, "1.4.3")),
        ((None, None), (None, None)),
        (None, (None, None)),
        ((1, 2), (None, None)),
    ],
)
def test_probe_reads_the_installed_and_available_versions(
    row: Any, expected: Any
) -> None:
    script = Script()
    script.rows = []

    class Cursor(Script):
        def fetchone(self) -> Any:
            return row

    assert hypopg.probe(Cursor()) == expected


# ------------------------------------------------------------------ _advise


def _issue(**kwargs: Any) -> ScanIssue:
    return ScanIssue(
        "public",
        "orders",
        "Seq Scan",
        "filter",
        "(orders.customer_id = $1)",
        "customer_id",
        show_schema=False,
        alias="orders",
        conditions=("(orders.customer_id = $1)",),
        **kwargs,
    )


class AdvisingCursor(Script):
    """A Script that also answers the index lookup and the extension probe."""

    def __init__(
        self,
        probe: tuple[str | None, str | None] = ("1.4.3", "1.4.3"),
        existing: list[Any] | None = None,
    ) -> None:
        super().__init__()
        self.probe = probe
        self.existing = existing or []
        self.plan = {"Node Type": "Seq Scan", "Total Cost": 100.0}

    def execute(self, sql: str, *params: Any) -> None:
        super().execute(sql, *params)
        if "FROM pg_index i" in sql:
            self.rows = self.existing
        elif sql == hypopg._PROBE:
            self.rows = [self.probe]
        elif "set_config('enable_seqscan'" in sql:
            self.rows = [("",)]
        elif sql.startswith("EXPLAIN"):
            import json

            plan = hypothetical(9.0) if self.hypothetical else self.plan
            self.rows = [(json.dumps([{"Plan": plan}]),)]

    def fetchall(self) -> list[Any]:
        return self.rows


def _advise(cursor: AdvisingCursor, mode: str = "auto", **kwargs: Any) -> Any:
    return indexes._advise(
        cursor,
        "SELECT * FROM orders WHERE customer_id = $1",
        [_issue(**kwargs)],
        {},
        Catalog(),
        AdvisorOptions(mode, 17),
        False,
    )


def test_advise_verifies_a_suggestion() -> None:
    cursor = AdvisingCursor()
    [issue], notice = _advise(cursor)
    assert notice is None and issue.advice is not None
    assert issue.advice.create_sql == "CREATE INDEX ON orders (customer_id)"
    assert issue.advice.verification is not None
    assert issue.advice.verification.used is True
    assert issue.advice.verification.cost_after == 9.0


def test_advise_off_never_asks_hypopg() -> None:
    cursor = AdvisingCursor()
    [issue], notice = _advise(cursor, "off")
    assert notice is None and issue.advice is not None
    assert issue.advice.verification is None
    assert not any("hypopg" in s for s in cursor.statements)


def test_advise_without_the_extension_says_so() -> None:
    cursor = AdvisingCursor(probe=(None, None))
    [issue], notice = _advise(cursor)
    assert issue.advice is not None and issue.advice.verification is None
    assert notice is not None and "not available on this server" in notice
    assert "apt install postgresql-17-hypopg" in notice


def test_advise_reads_the_probe_once_per_catalog() -> None:
    cursor = AdvisingCursor(probe=(None, "1.4.3"))
    catalog = Catalog()
    for _ in range(2):
        indexes._advise(
            cursor,
            "SELECT 1",
            [_issue()],
            {},
            catalog,
            AdvisorOptions("auto", 17),
            False,
        )
    assert cursor.statements.count(hypopg._PROBE) == 1
    assert catalog.hypopg == (None, "1.4.3")


def test_advise_on_creates_the_extension_when_it_is_only_available() -> None:
    cursor = AdvisingCursor(probe=(None, "1.4.3"))
    [issue], notice = _advise(cursor, "on")
    assert notice is None
    assert "CREATE EXTENSION IF NOT EXISTS hypopg" in cursor.statements
    assert issue.advice is not None and issue.advice.verification is not None


def test_advise_on_without_the_extension_available_only_says_so() -> None:
    cursor = AdvisingCursor(probe=(None, None))
    [_], notice = _advise(cursor, "on")
    assert notice is not None and "not available" in notice
    assert "CREATE EXTENSION IF NOT EXISTS hypopg" not in cursor.statements


def test_advise_skips_hypopg_when_an_index_blocks_the_suggestion() -> None:
    existing = [
        ("public", "orders", "orders_customer", "btree", True, ["customer_id"], None)
    ]
    cursor = AdvisingCursor(existing=existing)
    [issue], notice = _advise(cursor)
    assert notice is None and issue.advice is not None
    assert issue.advice.create_sql is None and issue.advice.skipped is not None
    assert not any("hypopg" in s for s in cursor.statements)


def test_advise_leaves_issues_without_conditions_alone() -> None:
    cursor = AdvisingCursor()
    bare = ScanIssue("public", "orders", "Seq Scan", "filter", "(a = $1)", "a")
    [issue], notice = indexes._advise(
        cursor, "SELECT 1", [bare], {}, Catalog(), AdvisorOptions(), False
    )
    assert issue is bare and notice is None


def test_advise_prefers_a_much_smaller_partial_index() -> None:
    full = Verification(used=True, size=1000)
    small = Verification(used=True, size=200)
    assert indexes._prefer_partial(full, small)
    assert not indexes._prefer_partial(full, Verification(used=True, size=300))
    assert not indexes._prefer_partial(full, Verification(used=False, size=10))
    assert not indexes._prefer_partial(Verification(used=False, size=1000), small)
    assert not indexes._prefer_partial(full, Verification(used=True, size=None))
    assert not indexes._prefer_partial(Verification(used=True, size=0), small)
