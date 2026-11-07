"""Run the Django accuracy corpus and print precision, recall and every mismatch.

    uv run --group django python scripts/django_accuracy_report.py [--findings]

Each configuration of ``tests/django_project/corpus/runner.py`` runs in a
pytest-django session of its own. ``QUERYGUARD_DJANGO_DB`` picks the
database as for the tests (SQLite when unset).
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

import django

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.django_project.settings")
django.setup()

from tests.corpus.metrics import known_reason  # noqa: E402
from tests.django_project.corpus import kit, runner  # noqa: E402
from tests.django_project.corpus.test_corpus import score  # noqa: E402


def _pct(value: float | None) -> str:
    return "  n/a" if value is None else f"{value * 100:5.1f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--findings", action="store_true", help="list every finding")
    args = parser.parse_args()
    scenarios = runner.load()
    bugs = sum(s.verdict == kit.BUG for s in scenarios)
    print(f"{len(scenarios)} scenarios: {bugs} BUG, {len(scenarios) - bugs} CLEAN")
    unexplained = 0
    with tempfile.TemporaryDirectory() as tmp:
        for config in runner.CONFIGS:
            results = runner.run(Path(tmp, config), [s.name for s in scenarios], config)
            print(f"\n{config}")
            for kind, tally in score(config, results).items():
                print(
                    f"  {kind:20} precision {_pct(tally.precision)}  "
                    f"recall {_pct(tally.recall)}  (tp {tally.tp}, fp {tally.fp}, fn {tally.fn})"
                )
            in_scope = runner.scope(config)
            for scenario in scenarios:
                result = results[scenario.name]
                expected = scenario.kinds & in_scope
                observed = result.kinds & in_scope
                if args.findings and result.findings:
                    print(f"    {scenario.name}: {sorted(result.findings)}")
                if expected != observed:
                    reason = known_reason(scenario, config)
                    unexplained += reason is None
                    print(
                        f"  MISMATCH {scenario.name}: expected {sorted(expected)}, "
                        f"got {sorted(observed)}" + (f" (known: {reason})" if reason else "")
                    )
    return 1 if unexplained else 0


if __name__ == "__main__":
    raise SystemExit(main())
