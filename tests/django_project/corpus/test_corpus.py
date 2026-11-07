"""The Django corpus as tests: every scenario against every configuration.

One pytest-django session per configuration runs all scenarios in a
subprocess; each (scenario, configuration) is then one test here. A scenario
whose ``KNOWN`` lists the configuration is a documented limitation and an
``xfail(strict=True)``, as in the SQLAlchemy corpus.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.corpus.metrics import Tally, known_reason
from tests.corpus.runner import Result

from . import kit, runner

pytestmark = pytest.mark.allow_nplusone

SCENARIOS = runner.load()
BY_NAME = {s.name: s for s in SCENARIOS}
_cache: dict[str, dict[str, Result]] = {}


def _results(
    config: str, tmp_path_factory: pytest.TempPathFactory
) -> dict[str, Result]:
    if config not in _cache:
        directory = Path(tmp_path_factory.mktemp(f"corpus-{config}"))
        _cache[config] = runner.run(directory, [s.name for s in SCENARIOS], config)
    return _cache[config]


def _cases() -> list[object]:
    cases = []
    for config in runner.CONFIGS:
        for scenario in SCENARIOS:
            reason = known_reason(scenario, config)
            marks = [pytest.mark.xfail(strict=True, reason=reason)] if reason else []
            cases.append(
                pytest.param(
                    config, scenario.name, marks=marks, id=f"{config}-{scenario.name}"
                )
            )
    return cases


@pytest.mark.parametrize("config, name", _cases())
def test_plugin_agrees_with_the_verdict(
    config: str, name: str, tmp_path_factory: pytest.TempPathFactory
) -> None:
    scenario = BY_NAME[name]
    result = _results(config, tmp_path_factory)[name]
    assert result.failed == bool(result.findings), (
        "the scenario's test failed without a finding"
        if result.failed
        else "a finding did not fail the scenario's test"
    )
    in_scope = runner.scope(config)
    observed = result.kinds & in_scope
    expected = scenario.kinds & in_scope
    assert observed == expected, (
        f"{scenario.verdict}: expected {sorted(expected)}, the plugin reported "
        f"{sorted(observed)}: {result.findings}"
    )


def test_the_corpus_is_balanced() -> None:
    bugs = [s for s in SCENARIOS if s.verdict == kit.BUG]
    clean = [s for s in SCENARIOS if s.verdict == kit.CLEAN]
    assert len(SCENARIOS) >= 40
    assert min(len(bugs), len(clean)) / max(len(bugs), len(clean)) >= 0.6


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_scenario_documents_its_verdict(scenario) -> None:  # type: ignore[no-untyped-def]
    first = scenario.doc.splitlines()[0]
    assert scenario.name.startswith(scenario.verdict.lower() + "_")
    assert first.startswith(
        scenario.verdict + (" " if scenario.verdict == kit.BUG else ":")
    )
    if scenario.verdict == kit.BUG:
        assert scenario.kinds, "a BUG names the finding kinds"
        assert all(kind in first for kind in scenario.kinds), first
    else:
        assert not scenario.kinds


def score(config: str, results: dict[str, Result]) -> dict[str, Tally]:
    """Precision and recall per kind, within the configuration's scope."""
    in_scope = runner.scope(config)
    tallies = {kind: Tally() for kind in sorted(in_scope)}
    for scenario in SCENARIOS:
        expected = scenario.kinds & in_scope
        observed = results[scenario.name].kinds & in_scope
        for kind in in_scope:
            if kind in expected and kind in observed:
                tallies[kind].tp += 1
            elif kind in observed:
                tallies[kind].fp += 1
            elif kind in expected:
                tallies[kind].fn += 1
    return tallies
