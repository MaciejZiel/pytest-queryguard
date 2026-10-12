from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

from .detectors import Detection, RelationshipFinding
from .events import CallSite, ScanEvent, StatementEvent
from .findings import KIND_MISSING_INDEX, KIND_RELATIONSHIP, KIND_REPEAT, Finding
from .indexes import table_label
from .listeners import Failure

_SQL_LIMIT = 200
_REPEAT_FIX = (
    "fix: fetch all rows in one query (WHERE id IN (...)) instead of in a loop"
)


_root: Path | None = None


def swap_root(new: Path | None) -> Path | None:
    """Set the directory paths are shown relative to; return the previous one.

    The plugin sets pytest's rootdir for the session, so messages built
    outside the plugin's own hooks (``QueryLog.assert_no_nplusone()``) show
    the same paths. Without it, they are relative to the working directory.
    """
    global _root
    old, _root = _root, new
    return old


def display_root() -> Path:
    return _root if _root is not None else Path.cwd()


def _display_path(filename: str, cwd: Path | None) -> str:
    base = cwd if cwd is not None else Path.cwd()
    path = Path(filename)
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return filename


def _location(site: CallSite, cwd: Path | None) -> str:
    return f"{_display_path(site.filename, cwd)}:{site.lineno} in {site.function}"


def _first_at(site: CallSite | None, cwd: Path | None) -> str:
    where = "<unknown location>" if site is None else _location(site, cwd)
    return f"  first at {where}"


def _block(finding: Detection, cwd: Path | None) -> list[str]:
    if isinstance(finding, RelationshipFinding):
        parent = finding.relationship.split(".", 1)[0]
        lines = [
            f"  {finding.relationship} was lazy-loaded {finding.count} times",
            _first_at(finding.first, cwd),
        ]
        if finding.sample_sql is not None:
            lines.append(f"  sql: {_truncate(finding.sample_sql)}")
        return [
            *lines,
            "  fix: load it with the parent query, e.g.",
            f"       select({parent}).options(selectinload({finding.relationship}))",
        ]
    return [
        f"  the same statement ran {finding.count} times",
        _first_at(finding.first, cwd),
        f"  {_truncate(finding.sql)}",
        f"  {_REPEAT_FIX}",
    ]


def make_finding(detection: Detection, *, cwd: Path | None = None) -> Finding:
    """The finding for one detector result, its message rendered for ``cwd``."""
    site = detection.first
    if isinstance(detection, RelationshipFinding):
        kind, label = KIND_RELATIONSHIP, detection.relationship
    else:
        kind, label = KIND_REPEAT, detection.sql
    return Finding(
        kind=kind,
        label=label,
        file=None if site is None else _display_path(site.filename, cwd),
        function=None if site is None else site.function,
        message="\n".join(_block(detection, cwd)),
        line=None if site is None else site.lineno,
        count=detection.count,
    )


def make_findings(
    detections: Iterable[Detection], *, cwd: Path | None = None
) -> list[Finding]:
    return [make_finding(d, cwd=cwd) for d in detections]


def format_findings(
    findings: Sequence[Finding | Detection], *, cwd: Path | None = None
) -> str:
    """The N+1 report; detector results are turned into findings first."""
    lines = ["queryguard: N+1 detected"]
    for finding in findings:
        if not isinstance(finding, Finding):
            finding = make_finding(finding, cwd=cwd)
        lines.append("")
        lines.append(finding.message)
    return "\n".join(lines)


def _truncate(sql: str) -> str:
    sql = " ".join(sql.split())
    if len(sql) > _SQL_LIMIT:
        return sql[:_SQL_LIMIT] + "..."
    return sql


def format_truncation(max_events: int) -> str:
    """The note for a test that ran more statements than the plugin stores."""
    return (
        f"queryguard: only the first {max_events} statements and relationship "
        "loads of this test were stored (queryguard_max_events); the counts "
        "above are lower bounds"
    )


def format_budget(
    limit: int,
    statements: list[StatementEvent],
    *,
    cwd: Path | None = None,
    total: int | None = None,
) -> str:
    """The budget failure; ``total`` is the real count when not all are stored."""
    shown = len(statements)
    got = shown if total is None else total
    lines = [f"queryguard: expected at most {limit} queries, got {got}", ""]
    for number, stmt in enumerate(statements, start=1):
        line = f"  {number}. {_truncate(stmt.sql)}"
        if stmt.callsite is not None:
            line += f"  ({_location(stmt.callsite, cwd)})"
        lines.append(line)
    if total is not None and total > shown:
        lines.append(
            f"  ... and {total - shown} more, not stored (queryguard_max_events)"
        )
    return "\n".join(lines)


def _scan_block(scan: ScanEvent, cwd: Path | None) -> list[str]:
    issue = scan.issue
    table = table_label(issue)
    where = f"with {issue.kind}"
    if issue.column is None:
        fix = "add an index that matches this condition"
        if issue.expression:
            fix += " (an expression index may be needed)"
    else:
        fix = f"CREATE INDEX ON {table} ({issue.column})"
    return [
        f"  {table} is read in full: {issue.node} {where} {_truncate(issue.condition)}",
        _first_at(scan.callsite, cwd),
        f"  sql: {_truncate(scan.sql)}",
        f"  fix: {fix}",
    ]


def make_index_finding(scan: ScanEvent, *, cwd: Path | None = None) -> Finding:
    """The finding for one missing-index scan, its message rendered for ``cwd``."""
    site = scan.callsite
    return Finding(
        kind=KIND_MISSING_INDEX,
        label=table_label(scan.issue),
        file=None if site is None else _display_path(site.filename, cwd),
        function=None if site is None else site.function,
        message="\n".join(_scan_block(scan, cwd)),
        line=None if site is None else site.lineno,
    )


def format_index_findings(scans: list[ScanEvent], *, cwd: Path | None = None) -> str:
    lines = ["queryguard: missing index"]
    for scan in scans:
        lines.append("")
        lines.append(make_index_finding(scan, cwd=cwd).message)
    lines.extend(
        [
            "",
            "  a sequential scan is fine on a small lookup table:",
            "  list it in queryguard_index_ignore_tables",
        ]
    )
    return "\n".join(lines)


def format_index_notes(
    notices: Iterable[str], errors: int, first_error: tuple[str, str] | None
) -> list[str]:
    """The session summary lines for statements the index check skipped."""
    lines = list(notices)
    if errors and first_error is not None:
        message, sql = first_error
        noun = "statement" if errors == 1 else "statements"
        lines.append(
            f"{errors} {noun} could not be explained; the first: {_truncate(message)}"
        )
        lines.append(f"  {_truncate(sql)}")
    return lines


def format_failure(failure: Failure) -> list[str]:
    """The session summary for a plugin error that stopped the measurement."""
    lines = [
        f"pytest-queryguard stopped measuring after an error in the {failure.source}:",
        f"  {failure.summary}",
    ]
    if failure.raised_at is not None:
        lines.append(f"  raised at {failure.raised_at}")
    lines.extend(
        [
            "Your tests were not affected. Tests that ran after the error were not",
            "checked for N+1 queries, budgets or missing indexes. Please report it at",
            "https://github.com/MaciejZiel/pytest-queryguard/issues",
        ]
    )
    return lines
