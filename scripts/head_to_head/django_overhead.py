"""Time a pytest-django suite under each Django N+1 tool, in interleaved rounds.

    python scripts/head_to_head/django_overhead.py --rounds 9 --out results/django-overhead.json

Run it with the Python of an environment that has the corpus's dependencies
(``uv sync --group django``) and every tool installed; the head-to-head
workflow does that in GitHub Actions, on one runner, so the tools share the
machine. Each round runs the suite once with no tool and once under each
tool, in the same order; a tool's number is the wall time of a whole pytest
process, best and median round.

The suite (written to a temporary directory) has ``--tests`` tests of the
test project's models with no N+1 in them, so no tool reports anything and
the time is the tools' bookkeeping: each test reads 18 books with
``select_related("author")``, gets three books one by one (below every
tool's threshold for a repeat: they run at different lines) and counts the
books. The rows are written by a fixture, as in a project's suite. Each tool
is set up as for the corpus (``django_corpus.py``, ``session_plugin.py``).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from importlib import metadata
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))

import common
from django_corpus import SETTINGS

TOOLS = ("none", "queryguard", "zeal", "nplusone-django")

SUITE = """\
import pytest

from tests.django_project.library.models import Book
from tests.django_project.library.seed import seed


@pytest.fixture
def library(db):
    seed()


@pytest.mark.parametrize("i", range({tests}))
def test_work(library, i):
    names = [book.author.name for book in Book.objects.select_related("author")]
    assert len(names) == 18
    first = Book.objects.get(pk=1)
    second = Book.objects.get(pk=2)
    third = Book.objects.get(pk=3)
    assert first.pk + second.pk + third.pk == 6
    assert Book.objects.count() == 18
"""


def versions() -> dict[str, str]:
    out = {"python": platform.python_version(), "platform": platform.platform()}
    for name in (
        "pytest",
        "django",
        "pytest-django",
        "pytest-queryguard",
        "django-zeal",
        "nplusone",
    ):
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pass
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--rounds", type=int, default=9)
    parser.add_argument("--tests", type=int, default=300)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--tools", default=",".join(TOOLS), help="comma-separated")
    args = parser.parse_args()
    tools = [t for t in args.tools.split(",") if t]
    seconds: dict[str, list[float]] = {tool: [] for tool in tools}
    failed: dict[str, str] = {}
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        (work / "test_suite.py").write_text(
            SUITE.format(tests=args.tests), encoding="utf-8"
        )
        (work / "h2h_django_settings.py").write_text(SETTINGS, encoding="utf-8")
        shutil.copy(HERE / "session_plugin.py", work / "session_plugin.py")
        for round_ in range(args.rounds):
            for key in tools:
                if key in failed:
                    continue
                tool = common.DJANGO_TOOLS[key]
                env = {
                    **os.environ,
                    "DJANGO_SETTINGS_MODULE": "h2h_django_settings",
                    "H2H_TOOL": key,
                    "H2H_OUT": str(work / "h2h.jsonl"),
                    "PYTHONPATH": os.pathsep.join(
                        p
                        for p in (str(work), str(ROOT), os.environ.get("PYTHONPATH"))
                        if p
                    ),
                }
                env.pop("PYTEST_ADDOPTS", None)
                command = [
                    sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
                    "-p", "no:randomly", "-q", "-p", "session_plugin", *tool.args,
                ]  # fmt: skip
                start = time.perf_counter()
                proc = subprocess.run(
                    command,
                    cwd=work,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                elapsed = time.perf_counter() - start
                if proc.returncode != 0:
                    failed[key] = (proc.stdout[-2000:] + proc.stderr[-2000:]).strip()
                    continue
                seconds[key].append(round(elapsed, 3))
            print(
                f"round {round_ + 1}: "
                + ", ".join(f"{k} {v[-1:]}" for k, v in seconds.items())
            )
    payload = {
        "suite": "django",
        "tests": args.tests,
        "rounds": args.rounds,
        "suite_seconds": {k: v for k, v in seconds.items() if v},
        "failed": failed,
        "versions": versions(),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    for key, error in failed.items():
        print(f"::warning::{key} failed: {common.first_line(error)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
