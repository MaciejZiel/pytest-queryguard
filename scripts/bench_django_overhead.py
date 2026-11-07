"""Time what the Django integration costs per statement and per row.

    uv run --group django python scripts/bench_django_overhead.py [--runs 7]

Three workloads on the test project's models (``tests/django_project``),
in-memory SQLite, Django's own migrations: ``get`` (``Book.objects.get(pk=...)``,
one statement each), ``rows`` (iterating 180 books with their authors: the
per-instance cost) and ``lazy`` (reading ``book.author`` of 180 books: a lazy
load each). Each runs in a fresh process in three modes: ``off`` (the plugin
imported, nothing installed), ``idle`` (installed, nothing measured: what code outside a
test pays) and ``on`` (a collector active, as in a test body). Prints the
median time per operation and the overhead against ``off``.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKLOADS = ("get", "rows", "lazy")
MODES = ("off", "idle", "on")


def child(mode: str, workload: str, repeat: int) -> float:
    sys.path.insert(0, str(ROOT))
    os.environ["DJANGO_SETTINGS_MODULE"] = "tests.django_project.settings"
    import django
    from django.core.management import call_command

    django.setup()
    call_command("migrate", run_syncdb=True, verbosity=0)
    from tests.django_project.library.models import Author, Book

    authors = Author.objects.bulk_create([Author(name=f"a{i}") for i in range(60)])
    Book.objects.bulk_create(
        [Book(title=f"b{i}", author=authors[i % 60]) for i in range(180)]
    )
    # imported in every mode, as pytest loads the plugin: "off" differs only
    # in what is installed and measured
    import pytest_queryguard.plugin  # noqa: F401

    collector = None
    if mode != "off":
        from pytest_queryguard import backends
        from pytest_queryguard import collector as collectors

        backends.install()
        if mode == "on":
            collector = collectors.QueryCollector(max_events=1000)
            collectors.push(collector)
        else:
            # hook in as a measurement would, then stop measuring
            probe = collectors.QueryCollector()
            collectors.push(probe)
            collectors.pop(probe)

    def get() -> int:
        for pk in range(1, 181):
            Book.objects.get(pk=pk)
        return 180

    def rows() -> int:
        return len([b.author.name for b in Book.objects.select_related("author")])

    def lazy() -> int:
        return len([b.author.name for b in Book.objects.all()])

    work = {"get": get, "rows": rows, "lazy": lazy}[workload]
    work()  # warm up
    timings = []
    for _ in range(repeat):
        if collector is not None:
            collector.statements.clear()
            collector.relationship_loads.clear()
        started = time.perf_counter()
        count = work()
        timings.append((time.perf_counter() - started) / count)
    return min(timings)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=7)
    parser.add_argument("--repeat", type=int, default=20)
    parser.add_argument("--child", nargs=2, metavar=("MODE", "WORKLOAD"))
    args = parser.parse_args()
    if args.child:
        print(json.dumps(child(*args.child, args.repeat)))
        return 0
    results: dict[tuple[str, str], list[float]] = {}
    for _ in range(args.runs):
        for workload in WORKLOADS:
            for mode in MODES:
                out = subprocess.run(
                    [sys.executable, __file__, "--child", mode, workload]
                    + ["--repeat", str(args.repeat)],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                results.setdefault((workload, mode), []).append(json.loads(out.stdout))
    print(f"{'workload':8} {'mode':5} {'per op':>10} {'overhead':>9}")
    for workload in WORKLOADS:
        base = statistics.median(results[(workload, "off")])
        for mode in MODES:
            value = statistics.median(results[(workload, mode)])
            print(
                f"{workload:8} {mode:5} {value * 1e6:8.1f} us "
                f"{(value / base - 1) * 100:+8.1f} %"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
