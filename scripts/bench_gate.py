"""Fail when the per-statement overhead of the measurement regresses.

Usage::

    uv run python scripts/bench_gate.py                  # compare with the baseline
    uv run python scripts/bench_gate.py --update         # record a baseline here
    uv run python scripts/bench_gate.py --record LOG...  # record one from gate logs

The gate checks the relative overhead (extra time per statement with the
measurement on, as a fraction of the time with it off) of the Core and ORM
loops of scripts/bench_overhead.py against scripts/bench_baseline.json. A
ratio is used instead of microseconds because it moves much less between
machines.

How it is measured (``bench_overhead.measure_interleaved``): each of
``--processes`` processes times blocks of ``--statements`` statements with the
measurement off and on, alternating, ``--rounds`` times, and keeps the fastest
block of each. Its ratio is (on - off) / off of those two; the gate takes the
median over the processes. Timing both in one process, interleaved, cancels
what drifts on a shared runner, and the fastest block is the one least slowed
by other work on the machine.

The default margin (2x the baseline plus 0.10) is for the CI job on every
pull request; the release gate passes a tighter one (scripts/release_gate.py).
A comparison over the limit is measured again, up to ``--attempts`` times in
all, and the best result of each loop counts: noise does not repeat in the
same direction every time, a regression does.

``--record`` writes the baseline as the mean of the ``ratios:`` lines in saved
logs of green gate runs (``gh run view RUN --log --job JOB > gate-1.log``),
so it comes from the runner that runs the gate and not from a laptop.

Run it on one operating system only; the baseline comes from Linux.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import bench_overhead

BASELINE = Path(__file__).parent / "bench_baseline.json"
FACTOR = 2.0
SLACK = 0.10
LOOPS = ("core", "orm")
# The line --record reads back from a log; printed once, after the last attempt.
RATIOS = re.compile(r"\bratios: (\{.*\})")
OK = "ok: per-statement overhead is within the allowed margin"


def _machine() -> str:
    """CPU model, CPU count, clock source and Python: what the ratio depends on."""
    model, clock = platform.processor() or "unknown CPU", "unknown"
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                model = line.split(":", 1)[1].strip()
                break
        source = "/sys/devices/system/clocksource/clocksource0/current_clocksource"
        clock = Path(source).read_text().strip()
    except OSError:
        pass
    return (
        f"{model}, {os.cpu_count()} CPUs, clock source {clock}, "
        f"Python {platform.python_version()}"
    )


def _measure(statements: int, rounds: int, processes: int) -> dict[str, float]:
    """The median over the processes of each loop's ratio, with the details printed."""
    results = bench_overhead.measure_interleaved(statements, rounds, processes)
    ratios: dict[str, float] = {}
    for name in LOOPS:
        per_process = [(on - off) / off for off, on in (r[name] for r in results)]
        ratios[name] = round(statistics.median(per_process), 3)
        off = statistics.median(r[name][0] for r in results)
        on = statistics.median(r[name][1] for r in results)
        print(
            f"  {name:<5} off {off:6.1f} us  on {on:6.1f} us per statement  "
            f"ratio {ratios[name]:.3f} "
            f"(processes {min(per_process):.3f} to {max(per_process):.3f})"
        )
    return ratios


def _limit(baseline: float, factor: float, slack: float) -> float:
    return baseline * factor + slack


def read_logs(paths: list[Path]) -> dict[str, float]:
    """The mean ratio of each loop over the ``ratios:`` lines of green gate logs."""
    found: list[dict[str, float]] = []
    for path in paths:
        text = path.read_text(errors="replace")
        if OK not in text:
            raise SystemExit(f"{path}: not the log of a passing overhead check")
        lines = [json.loads(m.group(1)) for m in RATIOS.finditer(text)]
        if not lines:
            raise SystemExit(f"{path}: no 'ratios:' line")
        found.extend(lines)
    return {
        name: round(statistics.fmean(ratios[name] for ratios in found), 3)
        for name in LOOPS
    }


def failures(
    ratios: dict[str, float], baseline: dict[str, float], factor: float, slack: float
) -> list[str]:
    return [
        f"{name}: overhead {ratio:.1%} > limit "
        f"{_limit(baseline[name], factor, slack):.1%} "
        f"(baseline {baseline[name]:.1%})"
        for name, ratio in ratios.items()
        if ratio > _limit(baseline[name], factor, slack)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--statements", type=int, default=1000, help="statements per timed block"
    )
    parser.add_argument(
        "--rounds", type=int, default=15, help="off and on blocks per process"
    )
    parser.add_argument("--processes", type=int, default=5)
    parser.add_argument(
        "--attempts", type=int, default=3, help="measurements at most, when over"
    )
    parser.add_argument("--factor", type=float, default=FACTOR)
    parser.add_argument("--slack", type=float, default=SLACK)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--update", action="store_true", help="write the baseline")
    action.add_argument(
        "--record",
        nargs="+",
        type=Path,
        metavar="LOG",
        help="write the baseline as the mean of these green gate logs",
    )
    args = parser.parse_args(argv)

    if args.record:
        recorded = read_logs(args.record)
        BASELINE.write_text(json.dumps(recorded, indent=2) + "\n")
        print(f"baseline written from {len(args.record)} log(s): {recorded}")
        return 0

    print(f"machine:   {_machine()}")
    print(
        f"method:    {args.processes} processes x {args.rounds} interleaved "
        f"rounds of {args.statements} statements, fastest block"
    )
    if args.update:
        current = _measure(args.statements, args.rounds, args.processes)
        BASELINE.write_text(json.dumps(current, indent=2) + "\n")
        print(f"baseline written: {current}")
        return 0

    baseline: dict[str, float] = json.loads(BASELINE.read_text())
    print(f"baseline:  {baseline}")
    best: dict[str, float] = {}
    problems: list[str] = []
    for attempt in range(1, max(args.attempts, 1) + 1):
        print(f"attempt {attempt}:")
        current = _measure(args.statements, args.rounds, args.processes)
        best = {
            name: min(current[name], best.get(name, current[name])) for name in LOOPS
        }
        problems = failures(best, baseline, args.factor, args.slack)
        if not problems:
            break
        print(f"  over the limit: {'; '.join(problems)}")
    print(f"ratios: {json.dumps(best)}")
    for problem in problems:
        print(f"FAIL {problem}")
    if not problems:
        print(OK)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
