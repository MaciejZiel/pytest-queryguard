"""scripts/bench_gate.py: the retry rule and recording a baseline from gate logs."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "bench_gate.py"

pytestmark = pytest.mark.skipif(
    not SCRIPT.is_file(), reason="scripts/ is not in the source distribution"
)


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("bench_gate_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    baseline = tmp_path / "bench_baseline.json"
    baseline.write_text(json.dumps({"core": 0.2, "orm": 0.3}))
    monkeypatch.setattr(module, "BASELINE", baseline)
    monkeypatch.setattr(module, "_machine", lambda: "test machine")
    return module


def measurements(gate: ModuleType, monkeypatch: pytest.MonkeyPatch, *results):
    pending = list(results)
    calls = []

    def measure(statements: int, rounds: int, processes: int) -> dict[str, float]:
        calls.append(processes)
        return pending.pop(0)

    monkeypatch.setattr(gate, "_measure", measure)
    return calls


def test_passes_on_the_first_attempt_within_the_limit(gate, monkeypatch, capsys):
    calls = measurements(gate, monkeypatch, {"core": 0.24, "orm": 0.36})
    assert gate.main(["--factor", "1.25", "--slack", "0"]) == 0
    assert len(calls) == 1
    out = capsys.readouterr().out
    assert 'ratios: {"core": 0.24, "orm": 0.36}' in out
    assert gate.OK in out


def test_noise_on_one_attempt_is_measured_again_and_the_best_counts(
    gate, monkeypatch, capsys
):
    calls = measurements(
        gate,
        monkeypatch,
        {"core": 0.30, "orm": 0.31},  # core over 0.25
        {"core": 0.22, "orm": 0.40},  # orm over 0.375, core fine now
    )
    assert gate.main(["--factor", "1.25", "--slack", "0"]) == 0
    assert len(calls) == 2
    assert 'ratios: {"core": 0.22, "orm": 0.31}' in capsys.readouterr().out


def test_a_regression_on_every_attempt_fails(gate, monkeypatch, capsys):
    slow = {"core": 0.30, "orm": 0.45}  # 1.5x the baseline
    calls = measurements(gate, monkeypatch, slow, slow, slow)
    assert gate.main(["--factor", "1.25", "--slack", "0"]) == 1
    assert len(calls) == 3
    out = capsys.readouterr().out
    assert "FAIL core: overhead 30.0% > limit 25.0%" in out
    assert "FAIL orm" in out
    assert gate.OK not in out


def test_record_writes_the_mean_of_green_gate_logs(gate, tmp_path, capsys):
    prefix = "overhead\tUNKNOWN STEP\t2026-11-01T00:00:00.0000000Z "
    logs = []
    for index, ratios in enumerate(
        ({"core": 0.2, "orm": 0.3}, {"core": 0.3, "orm": 0.4})
    ):
        log = tmp_path / f"gate-{index}.log"
        log.write_text(
            f"{prefix}attempt 1:\n{prefix}ratios: {json.dumps(ratios)}\n"
            f"{prefix}{gate.OK}\n"
        )
        logs.append(str(log))
    assert gate.main(["--record", *logs]) == 0
    assert json.loads(gate.BASELINE.read_text()) == {"core": 0.25, "orm": 0.35}


def test_record_refuses_a_failed_gate_log(gate, tmp_path):
    log = tmp_path / "gate.log"
    log.write_text('ratios: {"core": 0.5, "orm": 0.5}\nFAIL core: overhead\n')
    with pytest.raises(SystemExit, match="not the log of a passing"):
        gate.main(["--record", str(log)])
    assert json.loads(gate.BASELINE.read_text()) == {"core": 0.2, "orm": 0.3}
