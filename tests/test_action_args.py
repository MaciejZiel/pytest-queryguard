"""The action splits `pytest-args` with shlex and never evaluates it in a shell."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ACTION = Path(__file__).resolve().parent.parent / "action" / "action.yml"

pytestmark = [
    pytest.mark.allow_nplusone,
    pytest.mark.skipif(not ACTION.is_file(), reason="action/ is not available"),
    pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required"),
    # the action runs on Linux and macOS; symlinks need privileges on Windows
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell"),
]


def splitting_snippet() -> str:
    """The lines of the real action that turn QG_ARGS into the `args` array."""
    text = ACTION.read_text(encoding="utf-8")
    match = re.search(r"^ *args=\(\)\n.*?^ *fi\n", text, re.MULTILINE | re.DOTALL)
    assert match, "the args splitting block is missing from action.yml"
    assert "eval" not in text.split("run: |", 2)[-1].split("Find the SARIF")[0]
    return match.group(0)


def split_with_bash(qg_args: str, tmp_path: Path) -> tuple[list[str], str]:
    script = splitting_snippet() + 'printf "%s\\n" "${args[@]}"\n'
    environment = {**os.environ, "QG_ARGS": qg_args}
    # `python` in the action is whatever is on PATH; use this interpreter
    link = tmp_path / "bin"
    link.mkdir()
    (link / "python").symlink_to(sys.executable)
    environment["PATH"] = f"{link}{os.pathsep}{environment['PATH']}"
    result = subprocess.run(
        ["bash", "-c", script],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.splitlines(), result.stderr


def test_quoted_words_stay_together(tmp_path: Path) -> None:
    words, _ = split_with_bash('-k "a or b" -x tests/', tmp_path)
    assert words == ["-k", "a or b", "-x", "tests/"]


def test_empty_input_gives_no_arguments(tmp_path: Path) -> None:
    words, _ = split_with_bash("", tmp_path)
    assert [w for w in words if w] == []


def test_command_substitution_and_backticks_are_not_executed(tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    payload = f'-k "a or b" $(touch {marker}) `touch {marker}` $HOME'
    words, stderr = split_with_bash(payload, tmp_path)
    assert not marker.exists()
    assert words == [
        "-k",
        "a or b",
        "$(touch",
        f"{marker})",
        "`touch",
        f"{marker}`",
        "$HOME",
    ]
    assert stderr == ""
