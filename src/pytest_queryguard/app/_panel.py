"""The development panel: the last requests as JSON and as one HTML page.

Off by default. The page is self-contained (inline CSS and JS, no third-party
code) and renders every value with ``textContent``. It is a development aid,
not something to expose in production.
"""

from __future__ import annotations

import html
import json
import threading
from collections import deque
from typing import Any

from .. import __version__ as version
from . import _core
from ._core import RequestReport

LOOPBACK = frozenset(("127.0.0.1", "::1", "localhost", "testclient"))
HEADERS = (
    ("cache-control", "no-store"),
    ("x-content-type-options", "nosniff"),
    ("referrer-policy", "no-referrer"),
    (
        "content-security-policy",
        (
            "default-src 'none'; style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'none'"
        ),
    ),
)


class History:
    """The last ``size`` finished reports, newest last; thread-safe."""

    def __init__(self, size: int) -> None:
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            raise ValueError(f"panel_history must be an integer >= 1, got {size!r}")
        self._items: deque[RequestReport] = deque(maxlen=size)
        self._lock = threading.Lock()

    def add(self, report: RequestReport) -> None:
        with self._lock:
            self._items.append(report)

    def snapshot(self) -> tuple[RequestReport, ...]:
        with self._lock:
            return tuple(self._items)


def normalize_path(path: str) -> str:
    if not isinstance(path, str) or not path.startswith("/") or len(path) < 2:
        raise ValueError(
            f"panel_path must start with '/' and name a path, got {path!r}"
        )
    return path.rstrip("/")


def route(panel_path: str, path: str) -> str | None:
    """'html', 'json', 'missing' for a path under the panel; None otherwise."""
    if path in (panel_path, panel_path + "/"):
        return "html"
    if path == panel_path + "/requests.json":
        return "json"
    if path.startswith(panel_path + "/"):
        return "missing"
    return None


def allowed(client: str | None, loopback_only: bool) -> bool:
    return not loopback_only or client is None or client in LOOPBACK


def json_body(history: History) -> bytes:
    from ._listeners import _watched

    error = _core.internal_error()
    payload: dict[str, Any] = {
        "version": version,
        "watched_engines": len(_watched),
        "internal_error": f"{type(error).__name__}: {error}" if error else None,
        "requests": [r.to_dict() for r in reversed(history.snapshot())],
    }
    return json.dumps(payload, default=str).encode("utf-8")


def html_body(json_url: str) -> bytes:
    page = _PAGE.replace("__JSON_URL__", html.escape(json_url, quote=True))
    return page.replace("__VERSION__", html.escape(version)).encode("utf-8")


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>queryguard panel</title>
<style>
:root{--bg:#fbfbfa;--fg:#1d1d1b;--muted:#6b6b66;--line:#e2e1dc;--card:#fff;
--bad:#b3261e;--badbg:#fbe9e7;--ok:#2e6b34;--code:#f3f2ee;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ecebe6;--muted:#a09f99;
--line:#2f2f2c;--card:#1e1e1c;--bad:#ff8a80;--badbg:#3a1d1a;--ok:#8fd694;--code:#262624;
color-scheme:dark}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;padding:16px}
main{max-width:1100px;margin:0 auto}
header{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:baseline}
h1{font-size:18px;margin:0}
.note{color:var(--bad);font-weight:600}
.muted{color:var(--muted)}
.bar{display:flex;gap:12px;align-items:center;margin:12px 0}
button{font:inherit;padding:4px 10px;border:1px solid var(--line);
background:var(--card);color:var(--fg);border-radius:6px;cursor:pointer}
.wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;background:var(--card)}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);
white-space:nowrap}
th{font-weight:600;color:var(--muted)}
td.num{text-align:right;font-variant-numeric:tabular-nums}
tr.row{cursor:pointer}
tr.row:hover td{background:var(--code)}
.badge{display:inline-block;padding:0 6px;border-radius:9px;font-size:12px;
background:var(--badbg);color:var(--bad);font-weight:600}
.ok{color:var(--ok)}
td.detail{white-space:normal;background:var(--bg)}
pre{background:var(--code);padding:8px;border-radius:6px;overflow-x:auto;
margin:6px 0;white-space:pre-wrap;word-break:break-word}
ol{padding-left:24px;margin:6px 0}
li{margin:4px 0}
code{font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
</style></head>
<body><main>
<header><h1>queryguard panel</h1>
<span class="note">Development only: never enable this in production.</span>
<span class="muted">pytest-queryguard __VERSION__</span></header>
<div class="bar"><button id="refresh" type="button">Refresh</button>
<label><input id="auto" type="checkbox"> refresh every 2 s</label>
<span id="status" class="muted" role="status"></span></div>
<div class="wrap"><table><thead><tr><th>Time</th><th>Request</th><th>Status</th>
<th>Queries</th><th>DB time</th><th>Total</th><th>Findings</th></tr></thead>
<tbody id="rows"></tbody></table></div>
</main>
<script>
"use strict";
const url = "__JSON_URL__";
const expanded = new Set();
function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined && text !== null) e.textContent = String(text);
  if (cls) e.className = cls;
  return e;
}
function ms(s) { return s === null || s === undefined ? "" : (s * 1000).toFixed(1) + " ms"; }
function where(x) { return x.file ? " (" + x.file + ":" + x.line + " in " + x.function + ")" : ""; }
function detail(r) {
  const td = el("td", null, "detail"); td.colSpan = 7;
  if (r.error) td.append(el("p", "Incomplete: " + r.error, "note"));
  if (r.over_budget) td.append(el("p", "Over budget: " + r.statement_count + " statements, budget " + r.budget, "note"));
  for (const f of r.findings) td.append(el("pre", f.message));
  td.append(el("strong", "Statements" + (r.truncated ? " (first " + r.statements.length + " of " + r.statement_count + ")" : "")));
  const ol = el("ol");
  for (const q of r.statements) {
    const li = el("li");
    li.append(el("code", q.sql));
    li.append(el("div", ms(q.duration) + (q.relationship ? "  loads " + q.relationship : "") + where(q), "muted"));
    ol.append(li);
  }
  td.append(ol);
  return td;
}
function render(data) {
  const body = document.getElementById("rows");
  body.replaceChildren();
  for (const r of data.requests) {
    const tr = el("tr", null, "row");
    tr.tabIndex = 0;
    tr.append(el("td", new Date(r.started * 1000).toLocaleTimeString()));
    const name = el("td"); name.append(el("span", r.name));
    if (r.route && r.route !== r.path) name.append(el("span", "  " + r.route, "muted"));
    tr.append(name);
    tr.append(el("td", r.status));
    tr.append(el("td", r.statement_count + (r.budget !== null ? " / " + r.budget : ""), "num"));
    tr.append(el("td", ms(r.db_time), "num"));
    tr.append(el("td", ms(r.duration), "num"));
    const f = el("td");
    if (r.findings.length || r.over_budget) {
      const n = r.findings.length + (r.over_budget ? 1 : 0);
      f.append(el("span", n + (n === 1 ? " problem" : " problems"), "badge"));
    } else f.append(el("span", "none", "ok"));
    tr.append(f);
    const toggle = () => { expanded.has(r.id) ? expanded.delete(r.id) : expanded.add(r.id); render(data); };
    tr.addEventListener("click", toggle);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") toggle(); });
    body.append(tr);
    if (expanded.has(r.id)) { const d = el("tr"); d.append(detail(r)); body.append(d); }
  }
  const status = data.requests.length + " requests, " + data.watched_engines + " watched engines";
  document.getElementById("status").textContent =
    status + (data.internal_error ? "; switched off: " + data.internal_error : "");
}
async function load() {
  try {
    const res = await fetch(url, {cache: "no-store"});
    render(await res.json());
  } catch (e) {
    document.getElementById("status").textContent = "Could not load " + url + ": " + e;
  }
}
let timer = null;
document.getElementById("refresh").addEventListener("click", load);
document.getElementById("auto").addEventListener("change", (e) => {
  clearInterval(timer); timer = e.target.checked ? setInterval(load, 2000) : null;
});
load();
</script></body></html>
"""
