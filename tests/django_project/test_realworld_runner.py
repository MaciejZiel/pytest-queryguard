"""scripts/realworld_runner.py: the harness of the ``manage.py test`` projects.

The runners write what scripts/realworld_summary.py reads after a pytest
run: the outcomes as JUnit XML and, with the plugin, the findings of each
test as ``--queryguard-json`` keys. Checked on a project of the test suite's
own, as tests/django_project/test_runner.py runs one.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

from .test_runner import CASES, _project

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


def _summary() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "realworld_summary", SCRIPTS / "realworld_summary.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run(project: Path, out: Path, name: str, *args: str) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("QUERYGUARD_")}
    env.update(
        DJANGO_SETTINGS_MODULE="qg_settings",
        PYTHONPATH=os.pathsep.join([str(project), str(ROOT), str(SCRIPTS)]),
        PYTHONDONTWRITEBYTECODE="1",
        REALWORLD_OUT=str(out),
        REALWORLD_RUN=name,
    )
    env.pop("PYTHONWARNINGS", None)
    result = subprocess.run(
        [sys.executable, "-m", "django", "test", "-v", "2", *args],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    (out / f"{name}.log").write_text(result.stdout + result.stderr)
    (out / f"{name}.meta.json").write_text(
        json.dumps({"exit": result.returncode, "seconds": 1.0})
    )


def test_the_runners_leave_what_the_summary_reads(tmp_path: Path) -> None:
    (tmp_path / "project").mkdir()
    project = _project(tmp_path / "project", CASES, None)
    out = tmp_path / "out"
    out.mkdir()
    _run(project, out, "baseline", "--testrunner", "realworld_runner.Baseline")
    _run(
        project,
        out,
        "plugin",
        "--testrunner",
        "realworld_runner.Guarded",
        "--queryguard-mode",
        "warn",
    )
    summary = _summary().summarize(out, "demo", "v1")
    assert summary.problems == [], summary.report
    assert summary.harness == [], summary.report
    stats = summary.stats
    assert stats["baseline"]["failed"] == stats["plugin"]["failed"] == 1
    assert stats["baseline"]["skipped"] == stats["plugin"]["skipped"] == 1
    assert stats["baseline"]["passed"] == stats["plugin"]["passed"] == 5
    # test_n_plus_one, and test_fails_on_its_own, whose own failure comes first
    assert stats["findings"] == {"relationship-load": 2, "repeated-statement": 1}
    counts = json.loads((out / "queryguard.json").read_text())
    findings = counts["tests"]["cases.test_books.Books::test_n_plus_one"]["findings"]
    assert [(f["kind"], f["label"], f["file"]) for f in findings] == [
        ("relationship-load", "Book.author", "shop.py")
    ]
