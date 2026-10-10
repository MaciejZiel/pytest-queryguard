from __future__ import annotations

from pathlib import Path

from .detectors import Finding, RelationshipFinding
from .events import CallSite, StatementEvent

_SQL_LIMIT = 200
_REPEAT_FIX = (
    "fix: fetch all rows in one query (WHERE id IN (...)) instead of in a loop"
)


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


def _block(finding: Finding, cwd: Path | None) -> list[str]:
    if isinstance(finding, RelationshipFinding):
        parent = finding.relationship.split(".", 1)[0]
        return [
            f"  {finding.relationship} was lazy-loaded {finding.count} times",
            _first_at(finding.first, cwd),
            "  fix: load it with the parent query, e.g.",
            f"       select({parent}).options(selectinload({finding.relationship}))",
        ]
    return [
        f"  the same statement ran {finding.count} times",
        _first_at(finding.first, cwd),
        f"  {finding.sql}",
        f"  {_REPEAT_FIX}",
    ]


def format_findings(findings: list[Finding], *, cwd: Path | None = None) -> str:
    lines = ["queryguard: N+1 detected"]
    for finding in findings:
        lines.append("")
        lines.extend(_block(finding, cwd))
    return "\n".join(lines)


def _truncate(sql: str) -> str:
    sql = " ".join(sql.split())
    if len(sql) > _SQL_LIMIT:
        return sql[:_SQL_LIMIT] + "..."
    return sql


def format_budget(
    limit: int, statements: list[StatementEvent], *, cwd: Path | None = None
) -> str:
    lines = [
        f"queryguard: expected at most {limit} queries, got {len(statements)}",
        "",
    ]
    for number, stmt in enumerate(statements, start=1):
        line = f"  {number}. {_truncate(stmt.sql)}"
        if stmt.callsite is not None:
            line += f"  ({_location(stmt.callsite, cwd)})"
        lines.append(line)
    return "\n".join(lines)
