"""Put the head-to-head results together: a Markdown summary and one JSON file.

    python scripts/head_to_head/aggregate.py RESULTS_DIR --markdown summary.md --json results.json

RESULTS_DIR holds the ``TOOL.json`` files of ``run_corpus.py`` and, when the
overhead job ran, ``overhead.json`` of ``overhead.py`` (in any subdirectory:
the workflow downloads each job's artifact into its own). A tool whose file
is missing (its job failed, or it could not be installed) is listed as not
run. Needs no package beyond the standard library.

With ``--suite django`` it reads the files of ``django_corpus.py`` and
``django_overhead.py`` instead: one accuracy table per Django version.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common


def load(directory: Path) -> tuple[dict[str, dict], dict | None]:
    corpus: dict[str, dict] = {}
    overhead = None
    for path in sorted(directory.rglob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("suite") == "django":
            continue
        if "micro" in data:
            overhead = data
        elif data.get("tool") in common.TOOLS:
            corpus[data["tool"]] = data
    return corpus, overhead


def verdicts(data: dict) -> dict[str, common.Verdict]:
    return {
        name: common.Verdict(r["status"], frozenset(r["kinds"]), r.get("detail", ""))
        for name, r in data["results"].items()
    }


def build(
    corpus: dict[str, dict], overhead: dict | None, run_url: str = ""
) -> tuple[str, dict]:
    """The Markdown summary and the JSON payload."""
    lines = ["# Head-to-head: pytest-queryguard and other SQLAlchemy N+1 tools", ""]
    if run_url:
        lines += [f"Workflow run: {run_url}", ""]
    payload: dict[str, object] = {"run": run_url, "tools": {}, "overhead": overhead}
    if not corpus:
        lines.append("No corpus results.")
    else:
        # The scenarios every tool's run loaded; a tool that loaded others
        # (a different set of optional packages) is scored on the common set.
        names = set.intersection(*(set(d["scenarios"]) for d in corpus.values()))
        first = next(iter(corpus.values()))
        scenarios = {n: first["scenarios"][n] for n in sorted(names)}
        bugs = sum(m["verdict"] == "BUG" for m in scenarios.values())
        lines += [
            (
                f"## Accuracy on the corpus ({len(scenarios)} scenarios: {bugs} BUG, "
                f"{len(scenarios) - bugs} CLEAN)"
            ),
            "",
            (
                "Each tool with its defaults plus what its documentation says makes it fail a "
                "test on an N+1. `precision`: flagged BUG / all flagged. `recall (all BUG)`: over "
                "every BUG scenario, including kinds the tool does not claim. A scenario where the "
                "test failed for another reason than the tool's report is an error: never a pass."
            ),
            "",
        ]
        scores = {}
        for key in common.COMPARED:
            if key in corpus:
                scores[key] = common.score(
                    common.TOOLS[key], scenarios, verdicts(corpus[key])
                )
        lines += common.accuracy_tables(scores)
        missing = [common.TOOLS[k].label for k in common.COMPARED if k not in corpus]
        if missing:
            lines += [
                "",
                "Not run (job failed or tool not installable): " + ", ".join(missing),
            ]
        for key in ("none", "none-sa20"):
            if key not in corpus:
                continue
            base = common.score(common.TOOLS[key], scenarios, verdicts(corpus[key]))
            failing = [name for name, _ in base.errors]
            lines += [
                "",
                f"{common.TOOLS[key].label}, every plugin off: "
                + (
                    "every scenario passes."
                    if not failing
                    else f"{len(failing)} scenarios fail on their own: "
                    + ", ".join(f"`{n}`" for n in failing)
                ),
            ]
        lines += ["", "### Settings and versions", ""]
        for key in ("none", "none-sa20", *common.COMPARED):
            if key not in corpus:
                continue
            tool, data = common.TOOLS[key], corpus[key]
            v = data["versions"]
            pkg = {
                "queryguard": "pytest-queryguard",
                "queryguard-all": "pytest-queryguard",
                "queryspy": "queryspy",
                "querycount": "pytest-querycount",
                "sniffer": "n-plus-one-sniffer",
                "nplusone": "nplusone",
                "nplusone-sa20": "nplusone",
            }.get(key)
            version = f" {v.get(pkg, '?')}" if pkg else ""
            lines.append(
                f"- **{tool.label}**{version}: {tool.notes} Python {v.get('python')}, "
                f"SQLAlchemy {v.get('sqlalchemy')}, pytest {v.get('pytest')}."
            )
        lines += ["", "### Errors, false positives and misses per tool", ""]
        for key, s in scores.items():
            label = common.TOOLS[key].label
            lines.append(
                f"<details><summary>{label}: {len(s.errors)} errors, "
                f"{len(s.false_positives)} CLEAN flagged, {len(s.misses)} in-scope BUG missed"
                "</summary>\n"
            )
            for name, detail in s.errors:
                lines.append(f"- error `{name}`: {detail.replace('|', '/')}")
            for name in s.false_positives:
                lines.append(f"- CLEAN flagged `{name}`")
            for name in s.misses:
                lines.append(f"- missed `{name}`")
            lines.append("\n</details>\n")
        for key, s in scores.items():
            payload["tools"][key] = {  # type: ignore[index]
                "label": common.TOOLS[key].label,
                "versions": corpus[key]["versions"],
                "bug_flagged": s.bug_flagged,
                "bug_missed": s.bug_missed,
                "bug_error": s.bug_error,
                "clean_passed": s.clean_passed,
                "clean_flagged": s.clean_flagged,
                "clean_error": s.clean_error,
                "precision": s.precision,
                "recall": s.recall,
                "in_scope_recall": s.in_scope_recall,
                "kinds": {
                    k: None if t is None else vars(t) for k, t in s.kinds.items()
                },
                "errors": s.errors,
                "false_positives": s.false_positives,
                "misses": s.misses,
                "results": corpus[key]["results"],
            }
        payload["scenarios"] = scenarios
    lines += ["", "## Overhead", ""]
    if overhead is None:
        lines.append("No overhead results.")
    else:
        lines += [
            (
                f"{overhead.get('rounds')} rounds per tool, all tools on the same runner, "
                "interleaved; added time against the same run with no tool. Per statement: the "
                "best round; the suite: the best and the median round."
            ),
            "",
            *common.overhead_table(overhead),
        ]
        if overhead.get("failed"):
            # the last line of each error: the whole output is in the JSON
            lines += [
                "",
                "Failed: "
                + "; ".join(
                    f
                    if "\n" not in f
                    else f"{f.split(':', 1)[0]}: {common.first_line(f)}"
                    for f in overhead["failed"]
                ),
            ]
        if overhead.get("versions"):
            lines += ["", "```", json.dumps(overhead["versions"], indent=1), "```"]
    return "\n".join(lines) + "\n", payload


def load_django(directory: Path) -> tuple[dict[str, dict[str, dict]], list[dict]]:
    """The Django corpus results by Django version and tool, and the overheads."""
    corpus: dict[str, dict[str, dict]] = {}
    overheads = []
    for path in sorted(directory.rglob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("suite") != "django":
            continue
        if "suite_seconds" in data:
            overheads.append(data)
        elif data.get("tool") in common.DJANGO_TOOLS:
            version = ".".join(str(data["versions"].get("django", "?")).split(".")[:2])
            corpus.setdefault(version, {})[data["tool"]] = data
    return corpus, overheads


def _seconds_cell(runs: list[float], base: list[float]) -> str:
    if not runs or not base:
        return "failed"
    best, base_best = min(runs), min(base)
    mid, base_mid = sorted(runs)[len(runs) // 2], sorted(base)[len(base) // 2]
    return f"{(best - base_best) / base_best:+.1%} / {(mid - base_mid) / base_mid:+.1%}"


def _median(values: list[float]) -> float:
    return sorted(values)[len(values) // 2]


def _split_rows(data: dict, keys: list[str]) -> list[str]:
    """The fixed cost (a one-test suite) and the cost per test, against no tool."""
    runs, one = data["suite_seconds"], data.get("one_test_seconds") or {}
    tests = int(data.get("tests") or 0)
    if not one.get("none") or not runs.get("none") or tests < 2:
        return []
    base_one, base_all = _median(one["none"]), _median(runs["none"])
    base_each = (base_all - base_one) / (tests - 1)
    fixed, each = [], []
    for key in keys:
        if not one.get(key) or not runs.get(key):
            fixed.append("failed")
            each.append("failed")
            continue
        tool_one, tool_all = _median(one[key]), _median(runs[key])
        fixed.append(f"{(tool_one - base_one) * 1000:+.0f} ms")
        extra = (tool_all - tool_one) / (tests - 1) - base_each
        each.append(f"{extra * 1e6:+.0f} us ({extra / base_each:+.1%})")
    return [
        "| fixed cost (one-test suite, median) | " + " | ".join(fixed) + " |",
        "| added per test (median) | " + " | ".join(each) + " |",
    ]


def build_django(
    corpus: dict[str, dict[str, dict]], overheads: list[dict], run_url: str = ""
) -> tuple[str, dict]:
    """The Markdown summary and the JSON payload of the Django head-to-head."""
    tools = common.DJANGO_TOOLS
    kinds = (common.REL, common.REPEAT, common.COLUMN, common.WRITE, common.UNUSED)
    lines = ["# Head-to-head: pytest-queryguard and the Django N+1 tools", ""]
    if run_url:
        lines += [f"Workflow run: {run_url}", ""]
    payload: dict[str, object] = {"run": run_url, "django": {}, "overhead": overheads}
    if not corpus:
        lines.append("No corpus results.")
    for version, by_tool in sorted(corpus.items()):
        names = set.intersection(*(set(d["scenarios"]) for d in by_tool.values()))
        first = next(iter(by_tool.values()))
        scenarios = {n: first["scenarios"][n] for n in sorted(names)}
        bugs = sum(m["verdict"] == "BUG" for m in scenarios.values())
        lines += [
            (
                f"## Django {version}: the Django corpus ({len(scenarios)} scenarios: "
                f"{bugs} BUG, {len(scenarios) - bugs} CLEAN)"
            ),
            "",
        ]
        scores = {
            key: common.score(tools[key], scenarios, verdicts(by_tool[key]))
            for key in common.DJANGO_COMPARED
            if key in by_tool
        }
        lines += common.accuracy_tables(scores, tools, kinds)
        missing = [tools[k].label for k in common.DJANGO_COMPARED if k not in by_tool]
        if missing:
            lines += ["", "Not run (job failed): " + ", ".join(missing)]
        if "none" in by_tool:
            base = common.score(tools["none"], scenarios, verdicts(by_tool["none"]))
            failing = [name for name, _ in base.errors]
            lines += [
                "",
                "No tool: "
                + (
                    "every scenario passes."
                    if not failing
                    else f"{len(failing)} scenarios fail on their own: "
                    + ", ".join(f"`{n}`" for n in failing)
                ),
            ]
        lines += ["", "### Settings and versions", ""]
        for key in ("none", *common.DJANGO_COMPARED):
            if key not in by_tool:
                continue
            v = by_tool[key]["versions"]
            pkg = {
                "queryguard": "pytest-queryguard",
                "queryguard-all": "pytest-queryguard",
                "zeal": "django-zeal",
                "nplusone-django": "nplusone",
            }.get(key)
            version_text = f" {v.get(pkg, '?')}" if pkg else ""
            lines.append(
                f"- **{tools[key].label}**{version_text}: {tools[key].notes} "
                f"Python {v.get('python')}, Django {v.get('django')}, pytest {v.get('pytest')}."
            )
        lines += ["", "### Errors, false positives and misses per tool", ""]
        for key, sc in scores.items():
            lines.append(
                f"<details><summary>{tools[key].label}: {len(sc.errors)} errors, "
                f"{len(sc.false_positives)} CLEAN flagged, {len(sc.misses)} in-scope BUG "
                "missed</summary>\n"
            )
            for name, detail in sc.errors:
                lines.append(f"- error `{name}`: {detail.replace('|', '/')}")
            for name in sc.false_positives:
                kinds_seen = by_tool[key]["results"][name]["kinds"]
                lines.append(f"- CLEAN flagged `{name}` ({', '.join(kinds_seen)})")
            for name in sc.misses:
                lines.append(f"- missed `{name}`")
            lines.append("\n</details>\n")
        payload["django"][version] = {  # type: ignore[index]
            key: {
                "label": tools[key].label,
                "versions": by_tool[key]["versions"],
                "precision": sc.precision,
                "recall": sc.recall,
                "in_scope_recall": sc.in_scope_recall,
                "kinds": {
                    k: None if t is None else vars(t) for k, t in sc.kinds.items()
                },
                "errors": sc.errors,
                "false_positives": sc.false_positives,
                "misses": sc.misses,
                "results": by_tool[key]["results"],
            }
            for key, sc in scores.items()
        }
    lines += ["", "## Overhead", ""]
    if not overheads:
        lines.append("No overhead results.")
    for data in overheads:
        runs = data["suite_seconds"]
        base = runs.get("none") or []
        keys = [
            k
            for k in common.DJANGO_COMPARED
            if k in runs or k in data.get("failed", {})
        ]
        lines += [
            (
                f"Django {data['versions'].get('django')}, a {data.get('tests')}-test "
                f"pytest-django suite, {data.get('rounds')} interleaved rounds on one runner: "
                "added wall time against no tool, best round / median round; then that "
                "time split into a fixed cost per pytest run and a cost per test."
            ),
            "",
            "| | " + " | ".join(tools[k].label for k in keys) + " |",
            "|---|" + "---|" * len(keys),
            "| added wall time | "
            + " | ".join(_seconds_cell(runs.get(k) or [], base) for k in keys)
            + " |",
            *_split_rows(data, keys),
            "",
        ]
        for key, error in (data.get("failed") or {}).items():
            lines.append(f"- {tools[key].label} failed: {common.first_line(error)}")
    return "\n".join(lines) + "\n", payload


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("results", type=Path)
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument(
        "--suite", choices=("sqlalchemy", "django"), default="sqlalchemy"
    )
    args = parser.parse_args()
    url = ""
    if os.environ.get("GITHUB_RUN_ID"):
        url = (
            f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
            f"{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
        )
    if args.suite == "django":
        markdown, payload = build_django(*load_django(args.results), url)
    else:
        corpus, overhead = load(args.results)
        markdown, payload = build(corpus, overhead, url)
    args.markdown.write_text(markdown, encoding="utf-8")
    args.json.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
