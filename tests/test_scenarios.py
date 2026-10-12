"""Regression scenarios: what the plugin does with common ORM access patterns.

The scenarios in ``tests/scenarios`` come from a head-to-head comparison with
other N+1 tools (SQLite, sync and async). Each runs in its own pytester
session with the plugin's defaults; ``EXPECTED`` says whether the test must
pass or fail, and ``HINTS`` what the report must say. A scenario that passes
is a promise of no false positive; one that fails is a promise of a finding
with the right advice.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("greenlet")
pytest.importorskip("aiosqlite")

# pytester runs sessions in-process, so the outer plugin sees their queries too.
pytestmark = pytest.mark.allow_nplusone

DIR = Path(__file__).parent / "scenarios"

PASS, FAIL = "passed", "failed"

# Comments mark outcomes that are by design rather than a verdict on the code.
EXPECTED: dict[str, str] = {
    "test_01_lazy_n1_bad": FAIL,
    "test_02a_selectinload_ok": PASS,
    "test_02b_joinedload_ok": PASS,
    "test_02c_subqueryload_ok": PASS,
    "test_03_session_get_loop_bad": FAIL,
    "test_03b_session_get_loop_3_bad": PASS,  # three gets: below the threshold of 5
    "test_04_async_awaitable_attrs_bad": FAIL,
    "test_04b_async_selectin_ok": PASS,
    "test_04c_async_get_loop_bad": FAIL,
    "test_05a_repeat_x2": PASS,
    "test_05b_repeat_x3_batch_legit": PASS,
    "test_05c_repeat_x4": PASS,
    "test_05d_repeat_x5": FAIL,
    "test_05e_repeat_x6": FAIL,
    "test_06_legit_diff_shapes_ok": PASS,
    "test_07a_budget_exact_ok": PASS,
    "test_07b_budget_over_by_1_bad": FAIL,
    "test_08_m2m_lazy_bad": FAIL,
    "test_08b_m2m_selectin_ok": PASS,
    "test_09_selfref_parent_walk_bad": PASS,  # every node has the same parent: one load
    "test_09b_selfref_joined_ok": PASS,
    "test_09d_many_to_one_lazy_bad": FAIL,
    "test_09e_many_to_one_joinedload_ok": PASS,
    "test_09c_selfref_kids_lazy_bad": FAIL,
    "test_10a_dynamic_loop_bad": FAIL,
    "test_10b_writeonly_loop_bad": FAIL,
    "test_10c_raiseload_ok": PASS,
    "test_11a_deferred_loop_bad": FAIL,
    "test_11b_undefer_ok": PASS,
    "test_11c_expire_after_commit_refresh_bad": FAIL,
    "test_11d_expire_after_commit_few_ok": PASS,
    "test_11e_expire_on_commit_false_ok": PASS,
    "test_12a_insert_flush_per_row": PASS,  # write loops are opt-in
    "test_12b_add_all_one_flush_ok": PASS,
    "test_12c_core_insert_per_row": PASS,  # write loops are opt-in
    "test_12d_core_executemany_ok": PASS,
    "test_12e_budget_counts_writes_bad": FAIL,
    "test_13a_core_select_loop_bad": FAIL,
    "test_13b_count_loop_bad": FAIL,
    "test_14a_pagination_3_pages_legit": PASS,
    # Two different parents that each lazy-load once are reported at the default
    # relationship threshold of 2; raise queryguard_relationship_threshold.
    "test_14b_two_parents_legit": FAIL,
    "test_14c_same_parent_twice_ok": PASS,
    "test_14d_identity_map_get_ok": PASS,
    # One statement shape run 6 times, whatever the length of its IN list.
    "test_14e_in_list_varying_len_legit": FAIL,
    "test_14f_literal_binds_loop_bad": FAIL,
}

# Text the report of a failing scenario must contain, and text it must not.
HINTS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "test_01_lazy_n1_bad": (("selectinload(Parent.children)",), ()),
    "test_08_m2m_lazy_bad": (("selectinload(Post.tags)",), ()),
    "test_09d_many_to_one_lazy_bad": (
        ("Child.parent was lazy-loaded", "joinedload(Child.parent)"),
        ("selectinload",),
    ),
    "test_09c_selfref_kids_lazy_bad": (("selectinload(Node.kids)",), ()),
    "test_11a_deferred_loop_bad": (
        ("Child.blob was loaded on its own", "undefer(Child.blob)"),
        ("IN (", "same statement"),
    ),
    "test_11c_expire_after_commit_refresh_bad": (
        ("expire_on_commit=False",),
        ("IN (", "same statement"),
    ),
    "test_05d_repeat_x5": (("the same statement ran 5 times",), ()),
    "test_14f_literal_binds_loop_bad": (("the same statement ran 6 times",), ()),
    "test_07b_budget_over_by_1_bad": (("expected at most 3 queries, got 4",), ()),
    "test_12e_budget_counts_writes_bad": (("expected at most 2 queries, got 4",), ()),
}


def _run(pytester: pytest.Pytester, ini: str = "") -> dict[str, tuple[str, str]]:
    """Run every scenario once: name -> (outcome, report text)."""
    pytester.makeini(f"[pytest]\n{ini}")
    pytester.makepyfile(
        models=(DIR / "models_body.py").read_text(),
        test_scen=(DIR / "scenarios_body.py").read_text(),
    )
    pytester.makeconftest((DIR / "conftest_body.py").read_text())
    recorder = pytester.inline_run("-p", "no:cacheprovider", "-W", "ignore")
    found: dict[str, tuple[str, str]] = {}
    for report in recorder.getreports("pytest_runtest_logreport"):
        if report.when == "call" or (report.when == "setup" and report.failed):
            found[report.nodeid.rsplit("::", 1)[-1]] = (
                report.outcome,
                str(report.longrepr or ""),
            )
    return found


def _problems(
    found: dict[str, tuple[str, str]],
    expected: dict[str, str],
    hints: dict[str, tuple[tuple[str, ...], tuple[str, ...]]],
) -> list[str]:
    problems: list[str] = []
    for name in sorted(set(expected) | set(found)):
        if name not in found:
            problems.append(f"{name}: did not run")
            continue
        if name not in expected:
            problems.append(f"{name}: no expectation, add it to EXPECTED")
            continue
        outcome, text = found[name]
        if outcome != expected[name]:
            problems.append(f"{name}: expected {expected[name]}, got {outcome}")
            continue
        wanted, unwanted = hints.get(name, ((), ()))
        problems.extend(
            f"{name}: the report lacks {snippet!r}"
            for snippet in wanted
            if snippet not in text
        )
        problems.extend(
            f"{name}: the report has {snippet!r}"
            for snippet in unwanted
            if snippet in text
        )
    return problems


def test_scenarios_behave_as_promised(pytester: pytest.Pytester) -> None:
    problems = _problems(_run(pytester), EXPECTED, HINTS)
    assert not problems, "\n".join(problems)


# With the small-repeat rule on, a few runs of one statement from one line,
# each with other parameters, are findings. Pagination is still left alone.
SMALL = {
    **EXPECTED,
    "test_03b_session_get_loop_3_bad": FAIL,
    "test_05a_repeat_x2": FAIL,
    "test_05b_repeat_x3_batch_legit": FAIL,
    "test_05c_repeat_x4": FAIL,
}
SMALL_HINTS = {
    **HINTS,
    "test_05b_repeat_x3_batch_legit": (("ran 3 times with different parameters",), ()),
}


def test_scenarios_with_the_small_repeat_rule_on(pytester: pytest.Pytester) -> None:
    found = _run(pytester, "queryguard_small_repeat_threshold = 2\n")
    problems = _problems(found, SMALL, SMALL_HINTS)
    assert not problems, "\n".join(problems)
    assert found["test_14a_pagination_3_pages_legit"][0] == PASS


# Only relationship loads fail; the other kinds warn, so those scenarios pass.
WARN_ONLY = {
    **EXPECTED,
    "test_03_session_get_loop_bad": PASS,
    "test_04c_async_get_loop_bad": PASS,
    "test_05d_repeat_x5": PASS,
    "test_05e_repeat_x6": PASS,
    "test_10a_dynamic_loop_bad": PASS,  # dynamic and write-only loops are
    "test_10b_writeonly_loop_bad": PASS,  # plain repeated statements
    "test_11a_deferred_loop_bad": PASS,
    "test_11c_expire_after_commit_refresh_bad": PASS,
    "test_13a_core_select_loop_bad": PASS,
    "test_13b_count_loop_bad": PASS,
    "test_14e_in_list_varying_len_legit": PASS,
    "test_14f_literal_binds_loop_bad": PASS,
}


def test_scenarios_with_only_relationship_loads_failing(
    pytester: pytest.Pytester,
) -> None:
    found = _run(pytester, "queryguard_fail_on = relationship-load\n")
    problems = _problems(found, WARN_ONLY, {})
    assert not problems, "\n".join(problems)
