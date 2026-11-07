"""Compare the runs of .github/workflows/upstream-fixes.yml for one patch.

Reads the JUnit XML of the four runs (before and after the patch, each
without and with the plugin) and the plugin's JSON file of the two runs with
it, and writes a Markdown summary to stdout:

- the outcomes of each run, the tests that passed before the patch and do
  not pass after it, and the outcomes of the tests the patch adds;
- the findings by kind before and after;
- the findings the patch is meant to remove (the "focus": a kind, a regular
  expression on the finding's message and one on the test id), with samples;
- the tests whose number of statements changed.

Exit status 1 when the patch broke a test, a test it adds does not pass, or a
focus finding is left after it; 2 when no focus finding was there before it
(the problem was not reproduced); 0 otherwise. Standard library only: it runs
with python -I next to third-party code.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path


def junit_outcomes(path: Path) -> dict[str, str]:
    outcomes: dict[str, str] = {}
    if not path.is_file():
        return outcomes
    for case in ET.parse(path).getroot().iter("testcase"):
        test_id = f"{case.get('classname', '')}::{case.get('name', '')}"
        outcome = "passed"
        for child in case:
            if child.tag in ("failure", "error"):
                outcome = "failed"
                break
            if child.tag == "skipped":
                outcome = "skipped"
        outcomes[test_id] = outcome
    return outcomes


def load_findings(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("tests", {})


def counts(outcomes: dict[str, str]) -> str:
    c = Counter(outcomes.values())
    return f"{c['passed']} / {c['failed']} / {c['skipped']}"


def is_focus(test_id: str, finding: dict, args: argparse.Namespace) -> bool:
    if args.focus_kind and finding.get("kind") != args.focus_kind:
        return False
    if args.focus_message and not re.search(
        args.focus_message, finding.get("message", ""), re.DOTALL
    ):
        return False
    return not (args.focus_test and not re.search(args.focus_test, test_id))


def location(finding: dict) -> str:
    if finding.get("file"):
        return f"{finding['file']}:{finding.get('line')} in {finding.get('function')}"
    return "<unknown location>"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--focus-kind", default="")
    parser.add_argument("--focus-message", default="")
    parser.add_argument("--focus-test", default="")
    args = parser.parse_args()
    out: Path = args.dir

    runs = {
        name: junit_outcomes(out / f"{name}.xml")
        for name in ("before", "before-qg", "after", "after-qg")
    }
    before_json = load_findings(out / "before.json")
    after_json = load_findings(out / "after.json")

    lines = [f"## {args.title}", ""]
    meta = out / "meta.md"
    if meta.is_file():
        lines += [meta.read_text(encoding="utf-8").rstrip(), ""]

    lines += [
        "| run | passed / failed / skipped |",
        "|---|---|",
        f"| before the patch, without the plugin | {counts(runs['before'])} |",
        f"| before the patch, with the plugin | {counts(runs['before-qg'])} |",
        f"| after the patch, without the plugin | {counts(runs['after'])} |",
        f"| after the patch, with the plugin | {counts(runs['after-qg'])} |",
        "",
    ]

    broken = sorted(
        t
        for t, o in runs["before"].items()
        if o == "passed" and runs["after"].get(t, "missing") not in ("passed",)
    )
    added = sorted(set(runs["after"]) - set(runs["before"]))
    added_failing = [
        t
        for t in added
        if runs["after"][t] != "passed" or runs["after-qg"].get(t) != "passed"
    ]
    lines.append(
        f"Tests that passed before the patch and do not after it: {len(broken)}"
    )
    lines += [f"- `{t}`: {runs['after'].get(t, 'missing')}" for t in broken[:30]]
    lines.append("")
    lines.append(f"Tests the patch adds: {len(added)}")
    lines += [
        f"- `{t}`: {runs['after'][t]} without the plugin, "
        f"{runs['after-qg'].get(t, 'missing')} with it"
        for t in added
    ]
    lines.append("")

    kinds_before = Counter(
        f["kind"] for v in before_json.values() for f in v.get("findings", [])
    )
    kinds_after = Counter(
        f["kind"] for v in after_json.values() for f in v.get("findings", [])
    )
    lines += ["| finding kind | before | after |", "|---|---|---|"]
    for kind in sorted(set(kinds_before) | set(kinds_after)):
        lines.append(f"| `{kind}` | {kinds_before[kind]} | {kinds_after[kind]} |")
    lines.append("")

    def focus(data: dict[str, dict]) -> list[tuple[str, dict]]:
        return [
            (t, f)
            for t, v in data.items()
            for f in v.get("findings", [])
            if is_focus(t, f, args)
        ]

    focus_before, focus_after = focus(before_json), focus(after_json)
    lines.append(
        f"Findings this patch is about (kind `{args.focus_kind or 'any'}`, "
        f"message `{args.focus_message or '.*'}`, test `{args.focus_test or '.*'}`): "
        f"**{len(focus_before)} before, {len(focus_after)} after**"
    )
    for label, items in (("before", focus_before), ("after", focus_after)):
        for t, f in items[:15]:
            lines.append(
                f"- {label}: `{t}`: {f['kind']} {f.get('label')} x{f.get('count')} "
                f"at `{location(f)}`"
            )
    lines.append("")

    changed = []
    for t in sorted(set(before_json) & set(after_json)):
        b = before_json[t].get("statements", 0)
        a = after_json[t].get("statements", 0)
        if a != b:
            changed.append((t, b, a))
    changed.sort(key=lambda row: (-(row[1] - row[2]), row[0]))
    lines.append(f"Tests whose number of statements changed: {len(changed)}")
    if changed:
        lines += ["", "| test | statements before | after |", "|---|---|---|"]
        lines += [f"| `{t}` | {b} | {a} |" for t, b, a in changed[:40]]
    lines.append("")

    lint = out / "lint.md"
    if lint.is_file():
        lines += [lint.read_text(encoding="utf-8").rstrip(), ""]

    print("\n".join(lines))
    (out / "summary.json").write_text(
        json.dumps(
            {
                "title": args.title,
                "broken": broken,
                "added": {
                    t: [runs["after"][t], runs["after-qg"].get(t)] for t in added
                },
                "focus_before": len(focus_before),
                "focus_after": len(focus_after),
                "statements_changed": changed,
                "kinds_before": kinds_before,
                "kinds_after": kinds_after,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if broken or added_failing or focus_after:
        return 1
    if not focus_before:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
