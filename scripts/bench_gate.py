"""Fail when the per-statement overhead of the measurement regresses.

Usage::

    uv run python scripts/bench_gate.py                  # compare with the baseline
    uv run python scripts/bench_gate.py --update         # record this CPU's baseline
    uv run python scripts/bench_gate.py --record LOG...  # record from gate logs

The gate checks the relative overhead (extra time per statement with the
measurement on, as a fraction of the time with it off) of the Core and ORM
loops of scripts/bench_overhead.py against scripts/bench_baseline.json.

How it is measured (``bench_overhead.measure_interleaved``): each of
``--processes`` processes times blocks of ``--statements`` statements with the
measurement off and on, alternating, ``--rounds`` times, and keeps the fastest
block of each. Its ratio is (on - off) / off of those two; the gate takes the
median over the processes. Timing both in one process, interleaved, cancels
what drifts on a shared runner, and the fastest block is the one least slowed
by other work on the machine.

The ratio still depends on the CPU: on GitHub's ``ubuntu-latest`` runners it
repeats within about 0.01 on one CPU model but differs by up to a third
between models. So the baseline has an entry per CPU model (``machines``,
keyed by the ``model name`` of /proc/cpuinfo), and the top-level values, the
highest of those entries, are the baseline for a CPU without one.

The default margin (2x the baseline plus 0.10) is for the CI job on every
pull request; the release gate passes a tighter one (scripts/release_gate.py).
A comparison over the limit is measured again, up to ``--attempts`` times in
all, and the best result of each loop counts: noise does not repeat in the
same direction every time, a regression does.

``--record`` sets each CPU model's entry to the mean of the first attempt of
saved logs of the release gate's ``overhead`` job on it (``gh run view RUN
--log --job JOB > gate-1.log``, one job per file), so the baseline comes from
the runner and not from a laptop. Entries of other CPU models are kept.

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
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

import bench_overhead

BASELINE = Path(__file__).parent / "bench_baseline.json"
FACTOR = 2.0
SLACK = 0.10
LOOPS = ("core", "orm")
# What --record reads back from a log: the CPU, and each attempt's result.
# ("ratios:" is the result the gate decided on, the best of the attempts.)
MACHINE = re.compile(r"\bmachine: +(.+?), \d+ CPUs,")
MEASURED = re.compile(r"\bmeasured: (\{.*\})")
OK = "ok: per-statement overhead is within the allowed margin"
# The heading scripts/release_gate.py prints for this check: a log without
# it is not from the release gate, and the CI job's looser margin passes more.
RELEASE_BENCH = "== bench:"


def _cpu() -> str:
    """The CPU model, the key of its baseline."""
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown CPU"


def _machine() -> str:
    """CPU model, CPU count, clock source and Python: what the ratio depends on."""
    clock = "unknown"
    try:
        source = "/sys/devices/system/clocksource/clocksource0/current_clocksource"
        clock = Path(source).read_text().strip()
    except OSError:
        pass
    return (
        f"{_cpu()}, {os.cpu_count()} CPUs, clock source {clock}, "
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


def baseline_for(data: dict[str, Any], cpu: str) -> tuple[dict[str, float], bool]:
    """This CPU's baseline, and whether it has its own entry."""
    own = data.get("machines", {}).get(cpu)
    if own is not None:
        return {name: own[name] for name in LOOPS}, True
    return {name: data[name] for name in LOOPS}, False


def with_machines(
    data: dict[str, Any], machines: dict[str, dict[str, float]]
) -> dict[str, Any]:
    """The baseline with these entries set and the fallback recomputed."""
    merged = {**data.get("machines", {}), **machines}
    return {
        **{name: max(entry[name] for entry in merged.values()) for name in LOOPS},
        "machines": dict(sorted(merged.items())),
    }


def read_logs(
    paths: list[Path], known: set[str], even_failed: bool = False
) -> dict[str, dict[str, float]]:
    """Per CPU model, the mean first-attempt ratio of each loop in these logs.

    A log must hold one release-gate overhead job that passed; with
    ``even_failed``, a failed one too, but only for a CPU not in ``known``.
    """
    found: dict[str, list[dict[str, float]]] = defaultdict(list)
    for path in paths:
        text = path.read_text(errors="replace")
        if text.count(RELEASE_BENCH) != 1:
            raise SystemExit(
                f"{path}: not the log of one release-gate overhead job "
                "(gh run view RUN --log --job JOB)"
            )
        cpus = {match.group(1) for match in MACHINE.finditer(text)}
        measured = MEASURED.search(text)
        if len(cpus) != 1 or measured is None:
            raise SystemExit(f"{path}: no single 'machine:' or no 'measured:' line")
        cpu = cpus.pop()
        if OK not in text and not (even_failed and cpu not in known):
            raise SystemExit(
                f"{path}: not the log of a passing overhead check"
                + (f" ({cpu} already has an entry)" if even_failed else "")
            )
        found[cpu].append(json.loads(measured.group(1)))
    return {
        cpu: {
            name: round(statistics.fmean(ratios[name] for ratios in runs), 3)
            for name in LOOPS
        }
        for cpu, runs in found.items()
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


def _write(data: dict[str, Any]) -> None:
    BASELINE.write_text(json.dumps(data, indent=2) + "\n")


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
    action.add_argument(
        "--update", action="store_true", help="write this CPU's baseline"
    )
    action.add_argument(
        "--record",
        nargs="+",
        type=Path,
        metavar="LOG",
        help="write the baseline of each CPU from these green gate logs",
    )
    parser.add_argument(
        "--even-failed",
        action="store_true",
        help="with --record, also read failed logs (a CPU new to the baseline)",
    )
    args = parser.parse_args(argv)
    if args.even_failed and not args.record:
        parser.error("--even-failed needs --record")
    if min(args.statements, args.rounds, args.processes, args.attempts) < 1:
        parser.error("--statements, --rounds, --processes and --attempts must be >= 1")
    data: dict[str, Any] = json.loads(BASELINE.read_text())

    if args.record:
        recorded = read_logs(
            args.record, set(data.get("machines", {})), args.even_failed
        )
        _write(with_machines(data, recorded))
        for cpu, ratios in recorded.items():
            print(f"baseline written for {cpu}: {ratios}")
        return 0

    cpu = _cpu()
    print(f"machine:   {_machine()}")
    print(
        f"method:    {args.processes} processes x {args.rounds} interleaved "
        f"rounds of {args.statements} statements, fastest block"
    )
    if args.update:
        current = _measure(args.statements, args.rounds, args.processes)
        _write(with_machines(data, {cpu: current}))
        print(f"baseline written for {cpu}: {current}")
        return 0

    baseline, own = baseline_for(data, cpu)
    print(f"baseline:  {baseline}")
    if not own:
        print(
            "           (no entry for this CPU: the highest recorded; "
            "add one with --record, see docs/releasing.md)"
        )
    best: dict[str, float] = {}
    problems: list[str] = []
    for attempt in range(1, args.attempts + 1):
        print(f"attempt {attempt}:")
        current = _measure(args.statements, args.rounds, args.processes)
        print(f"  measured: {json.dumps(current)}")
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
    if problems and not own:
        print(
            f"{cpu} has no baseline of its own. If the code is unchanged, record "
            "one from this log: --record LOG --even-failed (docs/releasing.md)."
        )
    if not problems:
        print(OK)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
