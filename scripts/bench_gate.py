"""Fail when the per-statement overhead of the measurement regresses.

Usage::

    uv run python scripts/bench_gate.py            # compare with the baseline
    uv run python scripts/bench_gate.py --update   # record a new baseline

The gate reuses scripts/bench_overhead.py and checks the relative overhead
(extra time per statement with measurement on, as a fraction of the time with
it off) for the Core and ORM loops against scripts/bench_baseline.json. A
ratio is used instead of microseconds because it moves much less between
machines. The allowed margin is deliberately generous (2x the baseline plus
0.10), so noise on a shared CI runner does not fail the build but a real
regression, such as an accidental stack walk or a quadratic list scan on every
statement, does. A failing comparison is repeated once and the better result
counts.

Run it on one operating system only; the baseline comes from Linux.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import bench_overhead

BASELINE = Path(__file__).parent / "bench_baseline.json"
FACTOR = 2.0
SLACK = 0.10


def _ratios(statements: int, runs: int) -> dict[str, float]:
    results = bench_overhead.measure(statements, runs)
    return {name: round((on - off) / off, 3) for name, (off, on) in results.items()}


def _limit(baseline: float, factor: float, slack: float) -> float:
    return baseline * factor + slack


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--statements", type=int, default=3000)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--factor", type=float, default=FACTOR)
    parser.add_argument("--slack", type=float, default=SLACK)
    parser.add_argument("--update", action="store_true", help="write the baseline")
    args = parser.parse_args()

    current = _ratios(args.statements, args.runs)
    if args.update:
        BASELINE.write_text(json.dumps(current, indent=2) + "\n")
        print(f"baseline written: {current}")
        return 0

    baseline: dict[str, float] = json.loads(BASELINE.read_text())

    def failures(ratios: dict[str, float]) -> list[str]:
        return [
            f"{name}: overhead {ratio:.0%} > limit "
            f"{_limit(baseline[name], args.factor, args.slack):.0%} "
            f"(baseline {baseline[name]:.0%})"
            for name, ratio in ratios.items()
            if ratio > _limit(baseline[name], args.factor, args.slack)
        ]

    print(f"measured:  {current}\nbaseline:  {baseline}")
    problems = failures(current)
    if problems:
        print("over the limit, measuring again")
        again = _ratios(args.statements, args.runs)
        print(f"measured:  {again}")
        best = {name: min(current[name], again[name]) for name in current}
        problems = failures(best)
    for problem in problems:
        print(f"FAIL {problem}")
    if not problems:
        print("ok: per-statement overhead is within the allowed margin")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
