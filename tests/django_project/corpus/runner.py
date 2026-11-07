"""Run the Django corpus in a pytest-django session and read back the findings.

The conftest and the result format are the SQLAlchemy corpus's
(``tests/corpus/runner.py``); only the session body and the settings differ.
"""

from __future__ import annotations

import importlib
import os
import pkgutil
import subprocess
import sys
from pathlib import Path

from tests.corpus.runner import CONFTEST, Result, Scenario, read_results, session_env

from . import kit, scenarios

ROOT = Path(__file__).resolve().parents[3]
SETTINGS = "tests.django_project.settings"

DEFAULT_KINDS = frozenset({kit.REL, kit.REPEAT, kit.COLUMN})

# name -> (ini lines, the kinds that configuration adds to the default ones)
CONFIGS: dict[str, tuple[str, frozenset[str]]] = {
    "default": ("", frozenset()),
    "small-repeat": ("queryguard_small_repeat_threshold = 2\n", frozenset()),
    "write-loop": ("queryguard_write_loop_threshold = 2\n", frozenset({kit.WRITE})),
    "column-load-2": ("queryguard_column_load_threshold = 2\n", frozenset()),
    # Raises at the lazy load that makes an N+1, inside the scenario's code.
    "strict": ("queryguard_strict = true\n", frozenset()),
}


def scope(config: str) -> frozenset[str]:
    return DEFAULT_KINDS | CONFIGS[config][1]


def load() -> list[Scenario]:
    found = []
    for info in sorted(pkgutil.iter_modules(scenarios.__path__), key=lambda i: i.name):
        module = importlib.import_module(f"{scenarios.__name__}.{info.name}")
        found.append(Scenario(info.name, module))
    return found


def run(directory: Path, names: list[str], config: str) -> dict[str, Result]:
    """One pytest-django session over ``names`` with ``config``, in a subprocess."""
    directory.mkdir(parents=True, exist_ok=True)
    ini, _ = CONFIGS[config]
    (directory / "conftest.py").write_text(CONFTEST, encoding="utf-8")
    (directory / "pytest.ini").write_text(f"[pytest]\n{ini}", encoding="utf-8")
    body = Path(__file__).with_name("session_body.py").read_text(encoding="utf-8")
    (directory / "test_session.py").write_text(body, encoding="utf-8")
    out = directory / "corpus_out.json"
    env = {
        **os.environ,
        **session_env(names, out, "queryguard"),
        "DJANGO_SETTINGS_MODULE": SETTINGS,
        "QUERYGUARD_DJANGO_TEST_NAME": "test_qg_corpus_" + config.replace("-", "_"),
        "PYTHONPATH": f"{ROOT}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
    }
    env.pop("PYTEST_ADDOPTS", None)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "no:randomly"]
        + ["-W", "ignore", "-q"],
        cwd=directory,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if not out.exists():
        raise RuntimeError(
            f"the session wrote no results:\n{proc.stdout}\n{proc.stderr}"
        )
    return read_results(out)
