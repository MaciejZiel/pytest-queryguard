"""The shell steps of action/action.yml, run with crafted inputs and a fake gh."""

from __future__ import annotations

import json
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
]

# Contexts whose values someone outside the repository's writers can choose
# (a pull request's title, body or branch, a commit message, a comment).
UNTRUSTED = re.compile(
    r"github\.event\.(pull_request|issue|comment|review|review_comment|"
    r"head_commit|commits|discussion|pages|workflow_run)\b|github\.head_ref"
)


def steps() -> list[tuple[str, str]]:
    """(name, text) of each step of the composite action."""
    text = ACTION.read_text(encoding="utf-8")
    body = text.split("\n  steps:\n", 1)[1]
    parts = re.split(r"^    - name: ", body, flags=re.MULTILINE)[1:]
    return [(part.split("\n", 1)[0], part) for part in parts]


def run_block(step: str) -> str:
    """The `run: |` script of a step, dedented as YAML does."""
    match = re.search(r"^      run: \|\n((?:(?: {8}.*)?\n)+)", step, re.MULTILINE)
    assert match, "the step has no run block"
    return "\n".join(line[8:] for line in match.group(1).splitlines()) + "\n"


def script(name: str) -> str:
    for step_name, text in steps():
        if step_name == name:
            return run_block(text)
    raise AssertionError(f"no step {name!r} in action.yml")


def test_no_expression_inside_a_run_block() -> None:
    # A ${{ }} inside run: is pasted into the script before bash reads it, so
    # a branch name or title like a"; curl ... would run. Every value must
    # come in through env:.
    shell_steps = [(n, t) for n, t in steps() if "\n      run: |" in t]
    assert len(shell_steps) >= 8
    for name, text in shell_steps:
        assert "${{" not in run_block(text), name


def test_untrusted_contexts_only_reach_env() -> None:
    for name, text in steps():
        for line in text.splitlines():
            if UNTRUSTED.search(line):
                # an `if:` condition or an env value, never a script line
                stripped = line.strip()
                assert stripped.startswith(("&&", "||", "!(", "if:")) or re.match(
                    r"^[A-Z_]+: \$\{\{", stripped
                ), (name, line)


def test_every_shell_step_uses_bash_and_no_step_runs_after_a_cancel() -> None:
    text = ACTION.read_text(encoding="utf-8")
    # always() keeps a step running after the run was cancelled; !cancelled()
    # still runs it after a failed step
    assert "always()" not in text
    for name, step in steps():
        if "\n      run: |" in step:
            assert "\n      shell: bash\n" in step, name


def test_actions_are_pinned_by_commit() -> None:
    for line in ACTION.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("uses:"):
            assert re.search(r"@[0-9a-f]{40} # v\d", line), line


def run_script(
    name: str,
    tmp_path: Path,
    env: dict[str, str],
    *,
    check: bool = True,
    bin_dir: Path | None = None,
    cwd: Path | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    output = tmp_path / "github_output"
    output.write_text("")
    environment = {
        **os.environ,
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        **env,
    }
    if bin_dir is not None:
        environment["PATH"] = f"{bin_dir}{os.pathsep}{environment['PATH']}"
    # GitHub runs `bash --noprofile --norc -eo pipefail {0}`
    path = tmp_path / "step.sh"
    path.write_text(script(name))
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(path)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
    )
    if check:
        assert result.returncode == 0, result.stdout + result.stderr
    outputs = dict(
        line.split("=", 1) for line in output.read_text().splitlines() if "=" in line
    )
    return result, outputs


def inputs(**overrides: str) -> dict[str, str]:
    values = {
        "QG_FAIL": "true",
        "QG_UPLOAD": "true",
        "QG_COMPARE": "true",
        "QG_COMMENT": "true",
        "QG_TREND": "false",
        "QG_CATEGORY": "pytest-queryguard",
        "QG_LINT": "false",
        "QG_LINT_MODELS": "",
    }
    values.update(overrides)
    return values


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell")
class TestInputs:
    def test_the_defaults_pass(self, tmp_path: Path) -> None:
        _, outputs = run_script("Check the inputs", tmp_path, inputs())
        assert outputs["artifact"] == "queryguard-json-pytest-queryguard"

    @pytest.mark.parametrize("value", ["True", "FALSE", "false"])
    def test_any_case_of_true_and_false(self, tmp_path: Path, value: str) -> None:
        run_script("Check the inputs", tmp_path, inputs(QG_FAIL=value))

    @pytest.mark.parametrize(
        "variable", ["QG_FAIL", "QG_UPLOAD", "QG_COMPARE", "QG_COMMENT"]
    )
    @pytest.mark.parametrize("value", ["yes", "1", "", "true "])
    def test_a_switch_that_is_not_true_or_false_stops(
        self, tmp_path: Path, variable: str, value: str
    ) -> None:
        # `inputs.fail == 'true'` would read "yes" as false: a failed test run
        # would pass the job.
        result, _ = run_script(
            "Check the inputs", tmp_path, inputs(**{variable: value}), check=False
        )
        assert result.returncode == 1
        name = variable[3:].lower()
        assert f"::error::input {name} must be" in result.stdout

    @pytest.mark.parametrize(
        ("value", "runs"),
        [
            ("false", ""),
            ("False", ""),
            ("", ""),
            ("true", "10"),
            ("TRUE", "10"),
            ("2", "2"),
            ("08", "8"),
            ("100", "100"),
        ],
    )
    def test_valid_trend(self, tmp_path: Path, value: str, runs: str) -> None:
        _, outputs = run_script("Check the inputs", tmp_path, inputs(QG_TREND=value))
        assert outputs["trend-runs"] == runs

    # 2**64 + 10 would wrap around to 10 in bash's arithmetic
    @pytest.mark.parametrize(
        "value",
        ["1", "0", "101", "0010", "18446744073709551626", "ten", "5 ", "$(id)"],
    )
    def test_invalid_trend(self, tmp_path: Path, value: str) -> None:
        result, _ = run_script(
            "Check the inputs", tmp_path, inputs(QG_TREND=value), check=False
        )
        assert result.returncode == 1
        assert "::error::input trend must be" in result.stdout

    def test_an_empty_category_stops(self, tmp_path: Path) -> None:
        result, _ = run_script(
            "Check the inputs", tmp_path, inputs(QG_CATEGORY=""), check=False
        )
        assert result.returncode == 1

    def test_the_artifact_name_has_no_forbidden_characters(
        self, tmp_path: Path
    ) -> None:
        category = '/language:python|db="pg"*?<>\\x\r\nend'
        _, outputs = run_script(
            "Check the inputs", tmp_path, inputs(QG_CATEGORY=category)
        )
        artifact = outputs["artifact"]
        assert artifact == "queryguard-json--language-python-db=-pg------x--end"
        assert not set('":<>|*?\\/\r\n') & set(artifact)

    @pytest.mark.parametrize(
        ("value", "mode"),
        [("false", ""), ("FALSE", ""), ("true", "all"), ("True", "all")]
        + [("changed", "changed"), ("Changed", "changed")],
    )
    def test_valid_lint(self, tmp_path: Path, value: str, mode: str) -> None:
        _, outputs = run_script(
            "Check the inputs",
            tmp_path,
            inputs(QG_LINT=value, QG_LINT_MODELS="app.models"),
        )
        assert outputs["lint"] == mode

    @pytest.mark.parametrize("value", ["yes", "1", "", "true ", "all"])
    def test_invalid_lint(self, tmp_path: Path, value: str) -> None:
        result, _ = run_script(
            "Check the inputs",
            tmp_path,
            inputs(QG_LINT=value, QG_LINT_MODELS="app.models"),
            check=False,
        )
        assert result.returncode == 1
        assert "::error::input lint must be" in result.stdout

    @pytest.mark.parametrize(
        "models", ["app.models", " app.models  other_app.db.models ", "_m"]
    )
    def test_valid_lint_models(self, tmp_path: Path, models: str) -> None:
        run_script(
            "Check the inputs", tmp_path, inputs(QG_LINT="true", QG_LINT_MODELS=models)
        )

    @pytest.mark.parametrize(
        "models",
        ["", " ", "app..models", "app.", "1app", "-x", "app,models", "$(id)", "a;b"],
    )
    def test_invalid_lint_models(self, tmp_path: Path, models: str) -> None:
        result, _ = run_script(
            "Check the inputs",
            tmp_path,
            inputs(QG_LINT="changed", QG_LINT_MODELS=models),
            check=False,
        )
        assert result.returncode == 1
        assert "::error::input lint-models must be" in result.stdout

    def test_lint_models_are_not_checked_without_lint(self, tmp_path: Path) -> None:
        run_script("Check the inputs", tmp_path, inputs(QG_LINT_MODELS="$(id)"))


def fake_python(tmp_path: Path, code: int) -> Path:
    """A `python` that runs `-c` with this interpreter and exits `code` for pytest."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "python"
    fake.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = "-c" ]; then exec "{sys.executable}" "$@"; fi\n'
        'echo "pytest $*" >> "$QG_LOG"\n'
        f"exit {code}\n"
    )
    fake.chmod(0o755)
    return bin_dir


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell")
class TestRunPytest:
    def run(
        self, tmp_path: Path, code: int, args: str = ""
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, str], str]:
        log = tmp_path / "log"
        log.write_text("")
        result, outputs = run_script(
            "Run pytest",
            tmp_path,
            {
                "QG_ARGS": args,
                "QG_BASELINE": "",
                "QG_SARIF": "queryguard.sarif",
                "QG_JSON": "queryguard.json",
                "QG_LOG": str(log),
            },
            check=False,
            bin_dir=fake_python(tmp_path, code),
        )
        return result, outputs, log.read_text()

    @pytest.mark.parametrize("code", [0, 1, 2, 3, 4, 5])
    def test_the_exit_code_is_an_output_and_the_step_passes(
        self, tmp_path: Path, code: int
    ) -> None:
        result, outputs, log = self.run(tmp_path, code)
        # the step itself passes; "Fail when pytest failed" fails the job
        assert result.returncode == 0, result.stderr
        assert outputs["exit-code"] == str(code)
        assert "--queryguard-sarif=queryguard.sarif" in log
        warned = "::warning::pytest" in result.stdout.replace(
            "::warning::pytest did not write", ""
        )
        assert warned == (code >= 2)

    def test_an_unclosed_quote_does_not_run_pytest(self, tmp_path: Path) -> None:
        result, outputs, log = self.run(tmp_path, 0, args="-k 'oops tests/")
        assert result.returncode == 1
        assert log == ""
        assert "exit-code" not in outputs

    def test_a_shell_payload_is_one_pytest_argument(self, tmp_path: Path) -> None:
        marker = tmp_path / "pwned"
        result, _, log = self.run(
            tmp_path, 0, args=f"-k \"x; touch {marker}\" '$(touch {marker})'"
        )
        assert result.returncode == 0
        assert not marker.exists()
        assert f"-k x; touch {marker} $(touch {marker})" in log


def fake_gh(tmp_path: Path) -> Path:
    """A `gh` that answers from files, applies --jq with the real jq and logs writes."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "gh"
    fake.write_text(
        f"#!{sys.executable}\n"
        + r"""
import json, os, shutil, subprocess, sys
args = sys.argv[1:]
log = os.environ["FAKE_LOG"]
if args[:2] == ["api", "user"]:
    login = os.environ.get("FAKE_LOGIN", "")
    if login == "!":
        sys.stderr.write("gh: connection reset by peer\n")
        sys.exit(1)
    if not login:
        sys.stderr.write("gh: Resource not accessible by integration (HTTP 403)\n")
        sys.exit(1)
    print(login)
    sys.exit(0)
if "--paginate" in args:
    if os.environ.get("FAKE_LIST_FAIL"):
        sys.stderr.write("gh: Not Found (HTTP 404)\n")
        sys.exit(1)
    jq = args[args.index("--jq") + 1]
    with open(os.environ["FAKE_COMMENTS"]) as handle:
        pages = json.load(handle)
    for page in pages:
        out = subprocess.run(["jq", "-r", jq], input=json.dumps(page), text=True,
                             capture_output=True, check=True)
        sys.stdout.write(out.stdout)
    sys.exit(0)
with open(log, "a") as handle:
    handle.write(" ".join(a for a in args if not a.startswith("body=")) + "\n")
for a in args:
    if a.startswith("body=@"):
        shutil.copy(a[len("body=@"):], os.environ["FAKE_BODY"])
refused = os.environ.get("FAKE_PATCH_REFUSED", "").split()
if "PATCH" in args and args[args.index("PATCH") + 1].rsplit("/", 1)[1] in refused:
    sys.stderr.write("gh: Forbidden (HTTP 403)\n")
    sys.exit(1)
if os.environ.get("FAKE_WRITE_FAIL"):
    sys.stderr.write("gh: Resource not accessible by integration (HTTP 403)\n")
    sys.exit(1)
"""
    )
    fake.chmod(0o755)
    return bin_dir


MARK = "<!-- pytest-queryguard compare -->"


def comment(id_: int, login: str, body: str, kind: str = "Bot") -> dict[str, object]:
    return {"id": id_, "user": {"login": login, "type": kind}, "body": body}


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")
@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is required")
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell")
class TestComment:
    def run(
        self,
        tmp_path: Path,
        *,
        report: str = f"{MARK}\n### compared\n\nrows\n",
        trend: str | None = None,
        pages: list[list[dict[str, object]]] | None = None,
        **env: str,
    ) -> tuple[subprocess.CompletedProcess[str], list[str], str]:
        report_file = tmp_path / "report.md"
        report_file.write_text(report, encoding="utf-8")
        trend_file = tmp_path / "trend.md"
        if trend is not None:
            trend_file.write_text(trend, encoding="utf-8")
        comments = tmp_path / "comments.json"
        comments.write_text(json.dumps(pages if pages is not None else [[]]))
        log = tmp_path / "gh.log"
        log.write_text("")
        body = tmp_path / "posted.md"
        tmp = tmp_path / "temp"
        tmp.mkdir()
        result, _ = run_script(
            "Comment the comparison on the pull request",
            tmp_path,
            {
                "QG_REPO": "owner/repo",
                "QG_PR": "7",
                "QG_REPORT": str(report_file),
                "QG_TREND": str(trend_file) if trend is not None else "",
                "QG_TMP": str(tmp),
                "FAKE_LOG": str(log),
                "FAKE_COMMENTS": str(comments),
                "FAKE_BODY": str(body),
                **env,
            },
            check=False,
            bin_dir=fake_gh(tmp_path),
        )
        posted = body.read_text(encoding="utf-8") if body.exists() else ""
        return result, log.read_text().splitlines(), posted

    def test_first_run_posts(self, tmp_path: Path) -> None:
        result, calls, posted = self.run(tmp_path, trend="<details>trend</details>\n")
        assert result.returncode == 0, result.stderr
        assert calls == ["api -X POST repos/owner/repo/issues/7/comments -F"]
        assert posted.startswith(MARK + "\n")
        assert posted.endswith("rows\n\n<details>trend</details>\n")

    def test_a_later_run_updates_its_own_comment(self, tmp_path: Path) -> None:
        pages = [
            [
                # a person quoting the marker, another category, then ours
                comment(1, "alice", MARK + "\nquoted", kind="User"),
                comment(
                    2, "github-actions[bot]", "<!-- pytest-queryguard compare pg -->"
                ),
            ],
            [comment(3, "github-actions[bot]", MARK + "\nold")],
        ]
        result, calls, _ = self.run(tmp_path, pages=pages)
        assert result.returncode == 0, result.stderr
        assert calls == ["api -X PATCH repos/owner/repo/issues/comments/3 -F"]

    def test_a_personal_token_finds_the_comment_of_its_user(
        self, tmp_path: Path
    ) -> None:
        # Before, only github-actions[bot] was looked for, so a custom token
        # posted a new comment on every push.
        pages = [
            [
                comment(4, "github-actions[bot]", MARK + "\nother"),
                comment(5, "ci-user", MARK + "\nold", kind="User"),
            ]
        ]
        result, calls, _ = self.run(tmp_path, pages=pages, FAKE_LOGIN="ci-user")
        assert result.returncode == 0, result.stderr
        assert calls == ["api -X PATCH repos/owner/repo/issues/comments/5 -F"]

    def test_an_app_token_finds_the_comment_of_its_bot(self, tmp_path: Path) -> None:
        pages = [[comment(6, "my-app[bot]", MARK + "\nold")]]
        result, calls, _ = self.run(tmp_path, pages=pages)
        assert result.returncode == 0, result.stderr
        assert calls == ["api -X PATCH repos/owner/repo/issues/comments/6 -F"]

    def test_a_comment_this_token_may_not_edit_is_passed_over(
        self, tmp_path: Path
    ) -> None:
        # an earlier run with another bot's token; newest first, then a new one
        pages = [
            [
                comment(8, "github-actions[bot]", MARK + "\nold"),
                comment(9, "my-app[bot]", MARK + "\nnewer"),
            ]
        ]
        result, calls, _ = self.run(tmp_path, pages=pages, FAKE_PATCH_REFUSED="9 8")
        assert result.returncode == 0, result.stderr
        assert calls == [
            "api -X PATCH repos/owner/repo/issues/comments/9 -F",
            "api -X PATCH repos/owner/repo/issues/comments/8 -F",
            "api -X POST repos/owner/repo/issues/7/comments -F",
        ]
        assert "::warning" not in result.stdout

    def test_a_failed_user_lookup_writes_nothing(self, tmp_path: Path) -> None:
        # not a 403: a personal token whose user is unknown for the moment
        # must not fall back to bots and post a second comment
        pages = [[comment(5, "ci-user", MARK + "\nold", kind="User")]]
        result, calls, _ = self.run(tmp_path, pages=pages, FAKE_LOGIN="!")
        assert result.returncode == 0, result.stderr
        assert calls == []
        assert "::warning::the token's user could not be looked up" in result.stdout

    def test_a_refused_write_is_a_warning(self, tmp_path: Path) -> None:
        result, _, _ = self.run(tmp_path, FAKE_WRITE_FAIL="1")
        assert result.returncode == 0, result.stderr
        assert "::warning::the pull-request comment was not written" in result.stdout
        assert "pull-requests: write" in result.stdout

    def test_a_refused_listing_is_a_warning(self, tmp_path: Path) -> None:
        result, calls, _ = self.run(tmp_path, FAKE_LIST_FAIL="1")
        assert result.returncode == 0, result.stderr
        assert calls == []
        assert "::warning::the pull-request comments could not be listed" in (
            result.stdout
        )

    def test_a_trend_that_does_not_fit_is_left_out(self, tmp_path: Path) -> None:
        trend = "<details>\n" + "| `t` | ▁▂▃ |\n" * 6000 + "</details>\n"
        assert len(trend) > 65536
        result, _, posted = self.run(tmp_path, trend=trend)
        assert result.returncode == 0, result.stderr
        assert len(posted) <= 65536
        assert "rows\n" in posted
        assert "The trend is in the job summary" in posted
        assert "▁▂▃" not in posted

    def test_a_huge_comparison_is_cut_at_a_line(self, tmp_path: Path) -> None:
        line = "| `tests/test_x.py::test[ż" + "x" * 80 + "]` | 1 | 2 | +1 |\n"
        report = f"{MARK}\n### compared\n\n" + line * 2000
        assert len(report) > 65536
        result, calls, posted = self.run(tmp_path, report=report)
        assert result.returncode == 0, result.stderr
        assert len(posted) <= 65536
        assert posted.startswith(MARK + "\n")
        kept = posted.split("\n\n_The whole comparison", 1)[0]
        assert kept.endswith("+1 |\n")
        assert "The whole comparison is in the job summary" in posted
        assert calls == ["api -X POST repos/owner/repo/issues/7/comments -F"]

    def test_a_cut_inside_details_closes_it(self, tmp_path: Path) -> None:
        line = "| `tests/test_x.py::test_" + "y" * 100 + "` | 3 | 2 | -1 |\n"
        report = (
            f"{MARK}\n### compared\n\n<details><summary>fewer</summary>\n\n"
            + line * 1000
            + "\n</details>\n"
        )
        result, _, posted = self.run(tmp_path, report=report)
        assert result.returncode == 0, result.stderr
        assert len(posted) <= 65536
        assert posted.count("<details>") == posted.count("</details>") == 1
        assert posted.index("</details>") < posted.index("_The whole comparison")


LINT_MODELS = """\
from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customer"
    id: Mapped[int] = mapped_column(primary_key=True)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"))
    customer: Mapped[Customer] = relationship()
"""

LINT_ORDERS = """\
from sqlalchemy import select

from lintapp.models import Order


def names(session):
    return [o.customer.id for o in session.scalars(select(Order))]
"""


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")
@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is required")
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell")
class TestLint:
    """The static-check step, with the real check and a fake gh."""

    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        # the repository root; the action runs in its "repo" subdirectory
        root = tmp_path / "workspace"
        app = root / "repo" / "lintapp"
        app.mkdir(parents=True)
        (app / "__init__.py").write_text("")
        (app / "models.py").write_text(LINT_MODELS)
        (app / "orders.py").write_text(LINT_ORDERS)
        (app / "clean.py").write_text("x = 1\n")
        return root

    def run(
        self,
        tmp_path: Path,
        workspace: Path,
        *,
        mode: str = "all",
        paths: str = "lintapp",
        models: str = "lintapp.models",
        pr: str = "",
        files: list[dict[str, object]] | None = None,
        **env: str,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, str], list[str]]:
        # a directory per call, so that a test can run the step twice
        tmp_path = tmp_path / f"call{len(list(tmp_path.glob('call*')))}"
        tmp_path.mkdir()
        bin_dir = fake_gh(tmp_path)
        # the interpreter of the tests, which has the plugin installed (not a
        # symlink: a virtual environment is found next to the path run)
        python = bin_dir / "python"
        python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        python.chmod(0o755)
        pages = tmp_path / "files.json"
        pages.write_text(json.dumps([files or []]))
        log = tmp_path / "gh.log"
        log.write_text("")
        tmp = tmp_path / "temp"
        tmp.mkdir()
        result, outputs = run_script(
            "Run the static check",
            tmp_path,
            {
                "QG_LINT": mode,
                "QG_LINT_MODELS": models,
                "QG_LINT_PATHS": paths,
                "QG_PR": pr,
                "QG_REPO": "owner/repo",
                "QG_TMP": str(tmp),
                "GITHUB_WORKSPACE": str(workspace),
                "FAKE_LOG": str(log),
                "FAKE_COMMENTS": str(pages),
                **env,
            },
            check=False,
            bin_dir=bin_dir,
            cwd=workspace / "repo",
        )
        annotations = [
            line for line in result.stdout.splitlines() if line.startswith("::")
        ]
        return result, outputs, annotations

    def test_findings_are_annotations_relative_to_the_repository(
        self, tmp_path: Path, workspace: Path
    ) -> None:
        result, outputs, annotations = self.run(tmp_path, workspace)
        # findings are warnings: the step passes, the exit code is an output
        assert result.returncode == 0, result.stdout + result.stderr
        assert outputs["exit-code"] == "1"
        (warning,) = annotations
        assert warning.startswith(
            "::warning file=repo/lintapp/orders.py,line=7,col=15,"
        )
        assert "joinedload(Order.customer)" in warning
        assert "1 finding in 4 files" in result.stdout

    def test_changed_checks_only_the_changed_python_files_in_the_paths(
        self, tmp_path: Path, workspace: Path
    ) -> None:
        (workspace / "elsewhere.py").write_text(LINT_ORDERS)
        files = [
            {"filename": "repo/lintapp/clean.py", "status": "modified"},
            {"filename": "repo/lintapp/gone.py", "status": "removed"},
            {"filename": "repo/README.md", "status": "added"},
            {"filename": "elsewhere.py", "status": "added"},  # not in lint-paths
        ]
        result, outputs, annotations = self.run(
            tmp_path, workspace, mode="changed", pr="7", files=files
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert outputs["exit-code"] == "0"
        assert annotations == []
        assert "0 findings in 1 file" in result.stdout
        files.append({"filename": "repo/lintapp/orders.py", "status": "renamed"})
        result, outputs, annotations = self.run(
            tmp_path, workspace, mode="changed", pr="7", files=files
        )
        assert outputs["exit-code"] == "1"
        assert annotations[0].startswith("::warning file=repo/lintapp/orders.py,")
        assert "1 finding in 2 files" in result.stdout

    def test_changed_without_a_changed_python_file_does_not_run(
        self, tmp_path: Path, workspace: Path
    ) -> None:
        files = [{"filename": "repo/README.md", "status": "modified"}]
        result, outputs, _ = self.run(
            tmp_path, workspace, mode="changed", pr="7", files=files
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "exit-code" not in outputs
        assert "did not run" in result.stdout

    def test_changed_outside_a_pull_request_checks_everything(
        self, tmp_path: Path, workspace: Path
    ) -> None:
        _, outputs, annotations = self.run(tmp_path, workspace, mode="changed")
        assert outputs["exit-code"] == "1"
        assert len(annotations) == 1

    def test_a_refused_file_list_checks_everything(
        self, tmp_path: Path, workspace: Path
    ) -> None:
        result, outputs, annotations = self.run(
            tmp_path, workspace, mode="changed", pr="7", FAKE_LIST_FAIL="1"
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert outputs["exit-code"] == "1"
        assert annotations[0].startswith("::warning::the changed files")
        assert annotations[1].startswith("::warning file=repo/lintapp/orders.py,")

    @pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
    def test_a_checkout_below_the_workspace(
        self, tmp_path: Path, workspace: Path
    ) -> None:
        # actions/checkout with path: repo; GitHub names the files relative
        # to the repository, and so must the annotations
        subprocess.run(["git", "init", "-q", str(workspace / "repo")], check=True)
        files = [{"filename": "lintapp/orders.py", "status": "modified"}]
        result, outputs, annotations = self.run(
            tmp_path, workspace, mode="changed", pr="7", files=files
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert outputs["exit-code"] == "1"
        assert annotations[0].startswith("::warning file=lintapp/orders.py,")

    def test_too_many_changed_files_checks_everything(
        self, tmp_path: Path, workspace: Path
    ) -> None:
        files: list[dict[str, object]] = [
            {"filename": f"repo/f{i}.txt", "status": "added"} for i in range(3000)
        ]
        _, outputs, annotations = self.run(
            tmp_path, workspace, mode="changed", pr="7", files=files
        )
        assert outputs["exit-code"] == "1"
        assert annotations[0].startswith("::warning::the pull request changes 3000")

    def test_a_model_module_that_cannot_be_imported_fails_the_step(
        self, tmp_path: Path, workspace: Path
    ) -> None:
        result, outputs, annotations = self.run(
            tmp_path, workspace, models="lintapp.nothing"
        )
        assert result.returncode == 1
        assert outputs["exit-code"] == "2"
        assert "cannot import lintapp.nothing" in result.stderr
        assert annotations[-1].startswith("::error::the static check could not run")

    def test_paths_are_split_like_pytest_args(
        self, tmp_path: Path, workspace: Path
    ) -> None:
        odd = workspace / "repo" / "my dir"
        odd.mkdir()
        (odd / "-x.py").write_text(LINT_ORDERS)
        _, outputs, annotations = self.run(
            tmp_path, workspace, paths="'my dir/-x.py' lintapp/clean.py"
        )
        assert outputs["exit-code"] == "1"
        assert annotations[0].startswith("::warning file=repo/my dir/-x.py,")
        result, outputs, _ = self.run(tmp_path, workspace, paths="'my dir")
        assert result.returncode == 1
        assert "exit-code" not in outputs
        assert "lint-paths cannot be split" in result.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell")
class TestInstall:
    """What the install step gives pip, in both places the action lives."""

    def run(
        self, tmp_path: Path, action_path: Path, package: str = ""
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        log = tmp_path / "pip.log"
        fake = bin_dir / "python"
        fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$QG_LOG"\n')
        fake.chmod(0o755)
        result, _ = run_script(
            "Install pytest-queryguard",
            tmp_path,
            {
                "QG_PACKAGE": package,
                "QG_ACTION_PATH": str(action_path),
                "QG_LOG": str(log),
            },
            check=False,
            bin_dir=bin_dir,
        )
        return result, log.read_text() if log.exists() else ""

    def test_this_repository_installs_the_checkout(self, tmp_path: Path) -> None:
        root = tmp_path / "pytest-queryguard"
        (root / "action").mkdir(parents=True)
        (root / "pyproject.toml").write_text("")
        result, log = self.run(tmp_path, root / "action")
        assert result.returncode == 0, result.stdout + result.stderr
        assert log.splitlines()[-1] == f"{root / 'action'}/.."

    def test_the_action_repository_installs_its_wheel(self, tmp_path: Path) -> None:
        root = tmp_path / "pytest-queryguard-action"
        (root / "dist").mkdir(parents=True)
        wheel = root / "dist" / "pytest_queryguard-1.0.0-py3-none-any.whl"
        wheel.write_text("")
        result, log = self.run(tmp_path, root)
        assert result.returncode == 0, result.stdout + result.stderr
        assert log.splitlines()[-1] == str(wheel)

    def test_the_wheel_wins_over_a_project_above_the_action(
        self, tmp_path: Path
    ) -> None:
        # the Marketplace copy checked out inside a project of its own
        (tmp_path / "pyproject.toml").write_text("")
        root = tmp_path / "pytest-queryguard-action"
        (root / "dist").mkdir(parents=True)
        wheel = root / "dist" / "pytest_queryguard-1.0.0-py3-none-any.whl"
        wheel.write_text("")
        result, log = self.run(tmp_path, root)
        assert result.returncode == 0, result.stdout + result.stderr
        assert log.splitlines()[-1] == str(wheel)

    @pytest.mark.parametrize("count", [0, 2])
    def test_no_single_wheel_stops(self, tmp_path: Path, count: int) -> None:
        root = tmp_path / "pytest-queryguard-action"
        (root / "dist").mkdir(parents=True)
        for version in ["1.0.0", "1.0.1"][:count]:
            (
                root / "dist" / f"pytest_queryguard-{version}-py3-none-any.whl"
            ).write_text("")
        result, log = self.run(tmp_path, root)
        assert result.returncode != 0
        assert f"found {count} pytest-queryguard wheels" in result.stdout
        assert log == ""

    def test_a_package_input_wins(self, tmp_path: Path) -> None:
        result, log = self.run(
            tmp_path, tmp_path / "nowhere", "pytest-queryguard==1.0.0"
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert log.splitlines()[-1] == "pytest-queryguard==1.0.0"
