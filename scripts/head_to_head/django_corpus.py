"""Run the Django accuracy corpus under one tool and write its per-scenario verdicts.

    python scripts/head_to_head/django_corpus.py TOOL --out results/django-TOOL.json

TOOL is a key of ``common.DJANGO_TOOLS``. Run it with the Python of an
environment that has the corpus's dependencies (``uv sync --group django``)
and the tool installed; the head-to-head workflow does that in GitHub
Actions, one job per tool and Django version. Nothing here installs anything.

The session is the one of ``scripts/django_accuracy_report.py``
(``tests/django_project/corpus/runner.py``): one pytest-django test per
scenario, the seed rows and the scenario's setup in a fixture, its ``run()``
in the test, on SQLite. Its settings add the tool's app to
``INSTALLED_APPS`` (``h2h_django_settings.py``, written next to the
session); ``session_plugin.py`` wraps each test call in the tool;
``common.classify`` turns the outcomes into verdicts.

django-zeal names a model and a field (``N+1 detected on library.Book.author``):
the kind is read from that field here, where Django is set up with the same
models: ``get()`` is ``repeated-statement``, a field that is no relation
``column-load``, any other name ``relationship-load``.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from importlib import metadata
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.django_project.settings")

import django

django.setup()

import common
from tests.corpus.runner import CONFTEST, read_results, session_env
from tests.django_project.corpus import runner

DISTRIBUTIONS = (
    "pytest",
    "django",
    "djangorestframework",
    "pytest-django",
    "pytest-queryguard",
    "django-zeal",
    "nplusone",
)

SETTINGS = '''\
"""The corpus settings, with the app of the tool under measurement."""
import os

from tests.django_project.settings import *  # noqa: F403

_APPS = {"zeal": "zeal", "nplusone-django": "nplusone.ext.django"}
if os.environ.get("H2H_TOOL") in _APPS:
    INSTALLED_APPS = [*INSTALLED_APPS, _APPS[os.environ["H2H_TOOL"]]]  # noqa: F405
'''

ZEAL_REPORT = re.compile(r"N\+1 detected on (\w+)\.(\w+)\.(\S+?)(?: at |\s|$)")


def zeal_kinds(text: str) -> frozenset[str]:
    """The corpus kinds of the zeal reports in a failed test's text."""
    from django.apps import apps
    from django.core.exceptions import FieldDoesNotExist

    kinds = set()
    for app, model_name, name in ZEAL_REPORT.findall(text):
        if name == "get()":
            kinds.add(common.REPEAT)
            continue
        try:
            field = apps.get_model(app, model_name)._meta.get_field(name)
        except (LookupError, FieldDoesNotExist):
            # a reverse accessor without a related_name, a GenericForeignKey
            kinds.add(common.REL)
            continue
        kinds.add(common.REL if field.is_relation else common.COLUMN)
    return frozenset(kinds)


def versions() -> dict[str, str]:
    out = {"python": platform.python_version(), "platform": platform.platform()}
    for name in DISTRIBUTIONS:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pass
    if os.environ.get("GITHUB_SHA"):
        out["commit"] = os.environ["GITHUB_SHA"]
    return out


def write_session(work: Path, ini: str) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    (work / "conftest.py").write_text(CONFTEST, encoding="utf-8")
    (work / "pytest.ini").write_text(f"[pytest]\n{ini}", encoding="utf-8")
    body = (ROOT / "tests/django_project/corpus/session_body.py").read_text("utf-8")
    (work / "test_session.py").write_text(body, encoding="utf-8")
    (work / "h2h_django_settings.py").write_text(SETTINGS, encoding="utf-8")
    shutil.copy(HERE / "session_plugin.py", work / "session_plugin.py")
    return work / "corpus_out.json"


def run(tool: common.Tool, work: Path, timeout: int) -> dict[str, object]:
    scenarios = runner.load()
    names = [s.name for s in scenarios]
    ini = runner.CONFIGS[tool.config][0] if tool.config else ""
    out = write_session(work, ini)
    lines_path = work / "h2h.jsonl"
    env = {
        **os.environ,
        # the corpus conftest connects to pytest-queryguard only for "queryguard"
        **session_env(names, out, "queryguard" if tool.config else "other"),
        "DJANGO_SETTINGS_MODULE": "h2h_django_settings",
        "H2H_TOOL": tool.key,
        "H2H_OUT": str(lines_path),
        "PYTHONPATH": os.pathsep.join(
            p for p in (str(work), str(ROOT), os.environ.get("PYTHONPATH")) if p
        ),
    }
    env.pop("PYTEST_ADDOPTS", None)
    command = [
        sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "no:randomly",
        "-W", "ignore", "-q", "-p", "session_plugin", f"--timeout={timeout}", *tool.args,
    ]  # fmt: skip
    proc = subprocess.run(
        command, cwd=work, env=env, capture_output=True, text=True, check=False
    )
    lines = []
    if lines_path.exists():
        for raw in lines_path.read_text(encoding="utf-8").splitlines():
            lines.append(json.loads(raw))
    phases = common.group_lines(lines)
    findings: dict[str, frozenset[str]] = {}
    if tool.config and out.exists():
        findings = {name: r.kinds for name, r in read_results(out).items()}
    results = {}
    for name in names:
        own = phases.get(name, [])
        reported: frozenset[str] = frozenset()
        if tool.key == "zeal":
            reported = zeal_kinds(
                "\n".join(
                    p.get("longrepr", "") for p in own if p.get("outcome") == "failed"
                )
            )
        verdict = common.classify(tool, own, findings.get(name), reported)
        if not lines and proc.returncode:
            # the session never started a test: say why (the last line of its error)
            verdict = common.Verdict(
                common.ERROR,
                detail="session failed: "
                + common.first_line(proc.stderr or proc.stdout),
            )
        results[name] = {
            "status": verdict.status,
            "kinds": sorted(verdict.kinds),
            "detail": verdict.detail,
        }
    return {
        "suite": "django",
        "tool": tool.key,
        "label": tool.label,
        "requirement": tool.requirement,
        "args": list(tool.args),
        "versions": versions(),
        "scenarios": {
            s.name: {"verdict": s.verdict, "kinds": sorted(s.kinds)} for s in scenarios
        },
        "results": results,
        "returncode": proc.returncode,
        "output_tail": (proc.stdout[-3000:] + proc.stderr[-3000:]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("tool", choices=sorted(common.DJANGO_TOOLS))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--timeout", type=int, default=300, help="seconds per scenario (pytest-timeout)"
    )
    args = parser.parse_args()
    tool = common.DJANGO_TOOLS[args.tool]
    with tempfile.TemporaryDirectory() as tmp:
        payload = run(tool, Path(tmp), args.timeout)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    statuses = [r["status"] for r in payload["results"].values()]  # type: ignore[attr-defined]
    print(
        f"{tool.label}, Django {payload['versions'].get('django')}: "  # type: ignore[attr-defined]
        + ", ".join(f"{s} {statuses.count(s)}" for s in sorted(set(statuses)))
        + f" (pytest exit {payload['returncode']})"
    )
    if statuses.count(common.ERROR) == len(statuses):
        print(f"::warning::{tool.label}: every scenario is an error")
        print(payload["output_tail"], file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
