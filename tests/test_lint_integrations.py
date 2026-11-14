"""The files around the static check and the plugin's output: the pre-commit
hook, the editor problem matchers in docs/editor.md and the agent skill."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent

# pytester sessions in this file run in subprocesses; nothing to measure here.
pytestmark = pytest.mark.allow_nplusone

MODELS = """\
from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customer"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"))
    customer: Mapped[Customer] = relationship()
"""

ORDERS = """\
from sqlalchemy import select

from shop.models import Order


def names(session):
    result = []
    for order in session.scalars(select(Order)).all():
        result.append(order.customer.name)
    return result
"""

TEST = """\
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from shop.models import Base, Customer, Order
from shop.orders import names


def test_names():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for i in range(3):
            session.add(Order(customer=Customer(name=f"c{i}")))
        session.commit()
        assert len(names(session)) == 3
"""


@pytest.fixture
def project(pytester: pytest.Pytester) -> Path:
    pytester.makeini("[pytest]\n")
    shop = pytester.mkpydir("shop")
    (shop / "models.py").write_text(MODELS, encoding="utf-8")
    (shop / "orders.py").write_text(ORDERS, encoding="utf-8")
    pytester.makepyfile(test_shop=TEST)
    return pytester.path


def _lint(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest_queryguard.lint", *args],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )


# --- .pre-commit-hooks.yaml ---

HOOKS = ROOT / ".pre-commit-hooks.yaml"
needs_hooks = pytest.mark.skipif(
    not HOOKS.is_file(), reason=".pre-commit-hooks.yaml is not in the sdist"
)


def _hook() -> dict[str, str]:
    text = HOOKS.read_text(encoding="utf-8")
    (block,) = re.split(r"^- ", text, flags=re.MULTILINE)[1:]
    return dict(re.findall(r"^ *([a-z_]+): (.+)$", block, re.MULTILINE))


@needs_hooks
def test_the_hook_runs_the_check_on_python_files_in_one_process() -> None:
    hook = _hook()
    assert hook["id"] == "queryguard-lint"
    assert hook["language"] == "python"
    assert hook["types"] == "[python]"
    assert hook["require_serial"] == "true"
    # no pass_filenames: false, no always_run: pre-commit appends the files
    assert "pass_filenames" not in hook
    assert "always_run" not in hook


@needs_hooks
def test_the_hook_command_as_pre_commit_runs_it(project: Path) -> None:
    # pre-commit runs `entry args... files...` from the repository root
    command = [*shlex.split(_hook()["entry"]), "--models", "shop.models"]
    files = ["shop/__init__.py", "shop/models.py", "shop/orders.py", "test_shop.py"]
    result = subprocess.run(
        [sys.executable, *command[1:], *files],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )
    assert command[0] == "python"
    assert result.returncode == 1, result.stderr
    assert result.stdout.startswith("shop/orders.py:9:")
    clean = subprocess.run(
        [sys.executable, *command[1:], "shop/models.py"],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )
    assert clean.returncode == 0, clean.stderr
    # without args, the hook stops with a usage error that names --models
    bare = subprocess.run(
        [sys.executable, *command[1:-2], "shop/orders.py"],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )
    assert bare.returncode == 2
    assert "--models" in bare.stderr


# --- docs/editor.md: VS Code problem matchers ---


def _tasks() -> dict[str, dict[str, Any]]:
    text = (ROOT / "docs" / "editor.md").read_text(encoding="utf-8")
    (block,) = re.findall(r"```json\n(.*?)```", text, re.DOTALL)
    tasks = json.loads(block)["tasks"]
    return {task["label"]: task for task in tasks}


def _problems(matcher: dict[str, Any], output: str) -> list[dict[str, str]]:
    """Problems as VS Code finds them: each window of consecutive lines."""
    patterns = matcher["pattern"]
    if isinstance(patterns, dict):
        patterns = [patterns]
    lines = output.splitlines()
    found = []
    for start in range(len(lines) - len(patterns) + 1):
        problem: dict[str, str] = {}
        for pattern, line in zip(patterns, lines[start:], strict=False):
            match = re.search(pattern["regexp"], line)
            if match is None:
                break
            for key in ("file", "line", "column", "message"):
                if key in pattern:
                    problem[key] = match[pattern[key]]
        else:
            found.append(problem)
    return found


def test_the_pytest_problem_matcher_finds_the_finding(
    pytester: pytest.Pytester, project: Path
) -> None:
    task = _tasks()["pytest (queryguard)"]
    assert task["args"][:2] == ["-m", "pytest"]
    result = pytester.runpytest_subprocess(*task["args"][2:])
    assert result.ret == 1
    problems = _problems(task["problemMatcher"], "\n".join(result.outlines))
    assert problems == [
        {
            "message": "Order.customer was lazy-loaded 3 times",
            "file": "shop/orders.py",
            "line": "9",
        }
    ]
    assert (project / problems[0]["file"]).is_file()


def test_the_lint_problem_matcher_finds_the_finding(project: Path) -> None:
    task = _tasks()["queryguard lint"]
    assert task["args"][:2] == ["-m", "pytest_queryguard.lint"]
    result = _lint(project, "--models", "shop.models", "shop")
    assert result.returncode == 1, result.stderr
    problems = _problems(task["problemMatcher"], result.stdout)
    assert problems == [
        {
            "file": "shop/orders.py",
            "line": "9",
            "column": "29",
            "message": "Order.customer is lazy-loaded once per row (N+1)",
        }
    ]


# --- skills/pytest-queryguard/SKILL.md and docs/agents.md ---

SKILL = ROOT / "skills" / "pytest-queryguard" / "SKILL.md"
needs_skill = pytest.mark.skipif(
    not SKILL.is_file(), reason="skills/ is not in the sdist"
)


@needs_skill
def test_the_skill_has_front_matter() -> None:
    text = SKILL.read_text(encoding="utf-8")
    match = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, "SKILL.md starts with YAML front matter"
    fields = dict(re.findall(r"^([a-z]+): (.+)$", match[1], re.MULTILINE))
    assert set(fields) == {"name", "description"}
    # the name is the directory's, in lowercase letters, digits and hyphens
    assert fields["name"] == SKILL.parent.name
    assert re.fullmatch(r"[a-z0-9-]{1,64}", fields["name"])
    assert len(fields["description"]) <= 1024
    assert "N+1" in fields["description"]
    # plain YAML scalars, which strict parsers reject with ": " or " #" inside,
    # a ":" at the end or an indicator character at the start
    for value in fields.values():
        assert not re.search(r":(\s|$)|\s#", value), value
        assert value[0] not in "-?:,[]{}#&*!|>'\"%@`", value
    # short: an agent reads all of it
    assert len(text.splitlines()) < 150


@needs_skill
def test_the_options_the_agent_files_name_exist(pytester: pytest.Pytester) -> None:
    help_text = pytester.runpytest_subprocess("--help").stdout.str()
    texts = [
        SKILL.read_text(encoding="utf-8"),
        (ROOT / "docs" / "agents.md").read_text(encoding="utf-8"),
    ]
    for text in texts:
        flags = set(re.findall(r"--queryguard-[a-z-]+", text))
        assert {"--queryguard-json", "--queryguard-write-patch"} <= flags
        for flag in flags:
            assert flag in help_text, flag
