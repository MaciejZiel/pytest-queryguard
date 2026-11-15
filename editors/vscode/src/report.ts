// Reading a pytest-queryguard report: the `--queryguard-json` file or the
// `--queryguard-sarif` file. Nothing here imports vscode, so the unit tests
// run under plain Node.

export const DOCS_URL = "https://maciejziel.github.io/pytest-queryguard/";

// The same table as _KIND_DOCS in src/pytest_queryguard/findings.py.
const KIND_DOCS: Record<string, string> = {
  "relationship-load": "how-detection-works.html#relationship-load",
  "repeated-statement": "how-detection-works.html#repeated-statement",
  "column-load": "how-detection-works.html#column-load",
  "write-loop": "how-detection-works.html#write-loop",
  "missing-index": "how-detection-works.html#missing-index",
  "unused-eager-load": "unused-eager-load.html",
  "cartesian-load": "how-detection-works.html#cartesian-load",
  "unused-columns": "unused-columns.html",
};

/** The page and section of the documentation that explain a finding kind. */
export function docsUrl(kind: string): string {
  return DOCS_URL + (KIND_DOCS[kind] ?? "when-a-test-fails.html");
}

export type Level = "error" | "warning" | "note";

export interface Frame {
  file: string;
  line: number;
  function: string;
}

/** Text to insert at a 1-based line and column (a SARIF fix). */
export interface Insertion {
  file: string;
  line: number;
  column: number;
  text: string;
}

export interface Fix {
  description: string;
  insertions: Insertion[];
}

export interface Finding {
  kind: string;
  label: string;
  /** As the report has it: relative to pytest's rootdir, `/` separated. */
  file: string;
  /** 1-based. */
  line: number;
  function: string;
  /** The largest count in one test. */
  count: number;
  /** The count over all the tests. */
  total: number;
  /** How many tests made the finding. */
  testCount: number;
  /** The node ids of those tests, when the report names them. */
  tests: string[];
  level: Level;
  baselined: boolean;
  /** The callers, innermost first. */
  stack: Frame[];
  message: string;
  headline: string;
  sql?: string;
  hint?: string;
  docs: string;
  fix?: Fix;
}

export interface Report {
  format: "json" | "sarif";
  /** False when the run stopped early (only the JSON report says so). */
  complete: boolean;
  findings: Finding[];
}

export class ReportError extends Error {}

export interface MessageParts {
  headline: string;
  sql?: string;
  hint?: string;
  docs?: string;
}

/**
 * Picks the `sql:`, `fix:` and `docs:` lines out of a finding's message.
 * The wording of messages is not a stable interface of pytest-queryguard, so
 * every part is optional and the caller falls back to the whole message.
 */
export function parseMessage(message: string): MessageParts {
  const lines = message.replace(/\r\n/g, "\n").split("\n");
  const parts: MessageParts = { headline: (lines[0] ?? "").trim() };
  const sections: Partial<Record<"sql" | "fix" | "docs", string[]>> = {};
  let open: string[] | undefined;
  let indent = 0;
  for (const line of lines.slice(1)) {
    const match = /^(sql|fix|docs): ?(.*)$/.exec(line);
    if (match) {
      const key = match[1] as "sql" | "fix" | "docs";
      open = sections[key] = [match[2]];
      indent = key.length + 2;
      continue;
    }
    if (open && /^\s/.test(line) && line.trim()) {
      // continuation lines are indented under the text after "fix: "
      const strip = Math.min(indent, line.length - line.trimStart().length);
      open.push(line.slice(strip).trimEnd());
      continue;
    }
    open = undefined;
  }
  const join = (key: "sql" | "fix" | "docs") => {
    const text = sections[key]?.join("\n").trim();
    return text ? text : undefined;
  };
  parts.sql = join("sql");
  parts.hint = join("fix");
  parts.docs = join("docs");
  return parts;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function int(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? Math.trunc(value) : fallback;
}

/** Parses either report; the format is told from the content. */
export function parseReport(text: string): Report {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch (error) {
    throw new ReportError(`not JSON: ${(error as Error).message}`);
  }
  if (isRecord(data) && Array.isArray(data.runs)) {
    return parseSarif(data);
  }
  if (isRecord(data) && isRecord(data.tests)) {
    return parseJson(data);
  }
  throw new ReportError("neither a --queryguard-json nor a SARIF file");
}

function finish(partial: Omit<Finding, "headline" | "sql" | "hint" | "docs">): Finding {
  const parts = parseMessage(partial.message);
  return {
    ...partial,
    headline: parts.headline || `${partial.label} (${partial.kind})`,
    // a repeated statement has its SQL as the label and no sql: line
    sql: parts.sql ?? (partial.kind === "repeated-statement" ? partial.label : undefined),
    hint: parts.hint,
    docs: docsUrl(partial.kind),
  };
}

function parseJson(data: Record<string, unknown>): Report {
  if (data.format !== 1) {
    throw new ReportError(`--queryguard-json format ${String(data.format)} is not supported`);
  }
  // One finding per identity (kind, label, file, function), as in SARIF.
  const byIdentity = new Map<string, Finding>();
  for (const [nodeId, test] of Object.entries(data.tests as Record<string, unknown>)) {
    if (!isRecord(test) || !Array.isArray(test.findings)) continue;
    for (const raw of test.findings) {
      if (!isRecord(raw)) continue;
      const file = str(raw.file);
      const line = int(raw.line);
      if (!file || line < 1) continue;
      const kind = str(raw.kind);
      const label = str(raw.label);
      const fn = str(raw.function);
      const count = int(raw.count);
      const baselined = raw.baselined === true;
      const key = JSON.stringify([kind, label, file, fn]);
      const seen = byIdentity.get(key);
      if (seen) {
        seen.total += count;
        seen.testCount += 1;
        seen.tests.push(nodeId);
        seen.baselined = seen.baselined && baselined;
        if (count > seen.count) seen.count = count;
        if (line < seen.line) {
          seen.line = line;
        }
        continue;
      }
      const stack: Frame[] = Array.isArray(raw.stack)
        ? raw.stack.filter(isRecord).map((frame) => ({
            file: str(frame.file),
            line: int(frame.line),
            function: str(frame.function),
          }))
        : [];
      byIdentity.set(
        key,
        finish({
          kind,
          label,
          file,
          line,
          function: fn,
          count,
          total: count,
          testCount: 1,
          tests: [nodeId],
          level: "warning",
          baselined,
          stack,
          message: str(raw.message),
        }),
      );
    }
  }
  return {
    format: "json",
    complete: data.complete !== false,
    findings: [...byIdentity.values()],
  };
}

function sarifLevel(value: unknown): Level {
  return value === "error" || value === "note" ? value : "warning";
}

function parseSarifFix(raw: unknown): Fix | undefined {
  if (!Array.isArray(raw) || !isRecord(raw[0])) return undefined;
  const fix = raw[0];
  const insertions: Insertion[] = [];
  for (const change of Array.isArray(fix.artifactChanges) ? fix.artifactChanges : []) {
    if (!isRecord(change) || !isRecord(change.artifactLocation)) continue;
    const file = str(change.artifactLocation.uri);
    for (const replacement of Array.isArray(change.replacements) ? change.replacements : []) {
      if (!isRecord(replacement) || !isRecord(replacement.deletedRegion)) continue;
      const region = replacement.deletedRegion;
      const empty =
        region.endLine === undefined ||
        (region.endLine === region.startLine && region.endColumn === region.startColumn);
      // the plugin's fixes only insert; anything else is not applied
      if (!empty) return undefined;
      const text = isRecord(replacement.insertedContent) ? str(replacement.insertedContent.text) : "";
      const line = int(region.startLine);
      const column = int(region.startColumn, 1);
      if (!file || line < 1 || column < 1 || !text) return undefined;
      insertions.push({ file, line, column, text });
    }
  }
  if (!insertions.length) return undefined;
  const description = isRecord(fix.description) ? str(fix.description.text) : "";
  return { description: description || "Apply the suggested fix", insertions };
}

function parseSarif(data: Record<string, unknown>): Report {
  const findings: Finding[] = [];
  for (const run of data.runs as unknown[]) {
    if (!isRecord(run) || !Array.isArray(run.results)) continue;
    for (const result of run.results) {
      if (!isRecord(result)) continue;
      const ruleId = str(result.ruleId);
      if (!ruleId.startsWith("queryguard/")) continue;
      const location = Array.isArray(result.locations) ? result.locations[0] : undefined;
      if (!isRecord(location) || !isRecord(location.physicalLocation)) continue;
      const physical = location.physicalLocation;
      const file = isRecord(physical.artifactLocation) ? str(physical.artifactLocation.uri) : "";
      const line = isRecord(physical.region) ? int(physical.region.startLine) : 0;
      if (!file || line < 1) continue;
      const logical = Array.isArray(location.logicalLocations) ? location.logicalLocations[0] : undefined;
      const properties = isRecord(result.properties) ? result.properties : {};
      const total = int(properties.count);
      const message = isRecord(result.message) ? str(result.message.text) : "";
      findings.push(
        finish({
          kind: ruleId.slice("queryguard/".length),
          label: str(properties.label),
          file,
          line,
          function: isRecord(logical) ? str(logical.name) : "",
          count: int(properties.peakCount, total),
          total,
          testCount: int(properties.tests, 1),
          tests: [],
          level: sarifLevel(result.level),
          baselined: false,
          stack: sarifStack(result.codeFlows, file, line),
          message,
          fix: parseSarifFix(result.fixes),
        }),
      );
    }
  }
  return { format: "sarif", complete: true, findings };
}

/** The callers from the code flow (outermost first there), innermost first. */
function sarifStack(codeFlows: unknown, file: string, line: number): Frame[] {
  if (!Array.isArray(codeFlows) || !isRecord(codeFlows[0])) return [];
  const threadFlows = codeFlows[0].threadFlows;
  if (!Array.isArray(threadFlows) || !isRecord(threadFlows[0])) return [];
  const locations = threadFlows[0].locations;
  if (!Array.isArray(locations)) return [];
  const frames: Frame[] = [];
  for (const entry of locations) {
    if (!isRecord(entry) || !isRecord(entry.location)) continue;
    const loc = entry.location;
    if (!isRecord(loc.physicalLocation)) continue;
    const physical = loc.physicalLocation;
    const frame: Frame = {
      file: isRecord(physical.artifactLocation) ? str(physical.artifactLocation.uri) : "",
      line: isRecord(physical.region) ? int(physical.region.startLine) : 0,
      function: isRecord(loc.message) ? str(loc.message.text) : "",
    };
    if (frame.file && frame.line > 0) frames.push(frame);
  }
  // the last location is the finding itself
  const last = frames[frames.length - 1];
  if (last && last.file === file && last.line === line) frames.pop();
  return frames.reverse();
}

/** Markdown for the hover of a finding. */
export function hoverMarkdown(finding: Finding): string {
  const out: string[] = [];
  out.push(`**queryguard: ${escapeMarkdown(finding.headline)}**`);
  out.push("");
  const counts =
    finding.testCount > 1
      ? `${finding.total} times in ${finding.testCount} tests, up to ${finding.count} in one`
      : `${finding.count} times`;
  out.push(`\`${finding.kind}\` · ${counts}${finding.baselined ? " · accepted by the baseline" : ""}`);
  if (finding.sql) {
    out.push("", "```sql", finding.sql, "```");
  }
  if (finding.hint) {
    out.push("", "**Fix:**", "", "```", finding.hint, "```");
  }
  if (finding.fix) {
    out.push("", `Quick fix available: ${escapeMarkdown(finding.fix.description)}`);
  }
  if (finding.tests.length) {
    const shown = finding.tests.slice(0, 5).map((t) => `\`${t}\``);
    const more = finding.tests.length > 5 ? ` and ${finding.tests.length - 5} more` : "";
    out.push("", `Seen in ${shown.join(", ")}${more}`);
  }
  if (!finding.sql && !finding.hint && finding.message) {
    out.push("", "```", finding.message, "```");
  }
  out.push("", `[Documentation](${finding.docs})`);
  return out.join("\n");
}

/** The one-line text of the diagnostic. */
export function diagnosticMessage(finding: Finding): string {
  return finding.testCount > 1
    ? `${finding.headline} (in ${finding.testCount} tests)`
    : finding.headline;
}

function escapeMarkdown(text: string): string {
  return text.replace(/[\\`*_{}[\]<>#|]/g, (c) => `\\${c}`);
}
