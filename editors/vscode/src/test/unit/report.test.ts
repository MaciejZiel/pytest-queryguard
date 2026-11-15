import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { test } from "node:test";
import { diagnosticMessage, docsUrl, hoverMarkdown, parseMessage, parseReport, ReportError } from "../../report";

// out/test/unit -> the extension's directory
const FIXTURES = path.resolve(__dirname, "../../../test-fixtures/workspace");
const read = (name: string) => fs.readFileSync(path.join(FIXTURES, name), "utf8");

test("the JSON report gives one finding per identity with its SQL and fix", () => {
  const report = parseReport(read("queryguard.json"));
  assert.equal(report.format, "json");
  assert.equal(report.complete, true);
  const byKind = Object.fromEntries(report.findings.map((f) => [f.kind, f]));
  assert.deepEqual(Object.keys(byKind).sort(), ["relationship-load", "repeated-statement"]);

  const rel = byKind["relationship-load"];
  assert.equal(rel.file, "app/orders.py");
  assert.equal(rel.line, 10);
  assert.equal(rel.function, "list_orders");
  assert.equal(rel.count, 50);
  assert.equal(rel.headline, "Order.customer was lazy-loaded 50 times");
  assert.equal(rel.sql, "SELECT customer.id, customer.name FROM customer WHERE customer.id = ?");
  assert.equal(rel.hint, "load it with the parent query, e.g.\nselect(Order).options(joinedload(Order.customer))");
  assert.deepEqual(rel.tests, ["tests/test_orders.py::test_list_orders"]);
  assert.deepEqual(rel.stack, [{ file: "tests/test_orders.py", line: 7, function: "test_list_orders" }]);
  assert.equal(rel.docs, "https://maciejziel.github.io/pytest-queryguard/how-detection-works.html#relationship-load");
  assert.equal(rel.fix, undefined);

  // a repeated statement's SQL is its label
  const rep = byKind["repeated-statement"];
  assert.equal(rep.sql, rep.label);
  assert.equal(rep.hint, "fetch all rows in one query (WHERE id IN (...)) instead of in a loop");
});

test("the SARIF report gives the level, the callers and the fix", () => {
  const report = parseReport(read("queryguard.sarif"));
  assert.equal(report.format, "sarif");
  const rel = report.findings.find((f) => f.kind === "relationship-load");
  assert.ok(rel);
  assert.equal(rel.level, "error");
  assert.equal(rel.label, "Order.customer");
  assert.equal(rel.line, 10);
  assert.equal(rel.count, 50);
  assert.equal(rel.sql, "SELECT customer.id, customer.name FROM customer WHERE customer.id = ?");
  assert.deepEqual(rel.stack, [{ file: "tests/test_orders.py", line: 7, function: "test_list_orders" }]);
  assert.deepEqual(rel.fix, {
    description: "Load Order.customer with the parent query: joinedload(Order.customer)",
    insertions: [
      { file: "app/orders.py", line: 2, column: 35, text: ", joinedload" },
      { file: "app/orders.py", line: 9, column: 47, text: ".options(joinedload(Order.customer))" },
    ],
  });
});

function jsonReport(tests: Record<string, unknown[]>, extra: Record<string, unknown> = {}): string {
  return JSON.stringify({
    format: 1,
    complete: true,
    ...extra,
    tests: Object.fromEntries(Object.entries(tests).map(([id, findings]) => [id, { findings }])),
  });
}

const finding = (over: Record<string, unknown> = {}) => ({
  kind: "relationship-load",
  label: "Order.customer",
  count: 5,
  file: "app/orders.py",
  function: "list_orders",
  line: 10,
  stack: [],
  baselined: false,
  message: "Order.customer was lazy-loaded 5 times",
  ...over,
});

test("the same finding from several tests is merged", () => {
  const report = parseReport(
    jsonReport({
      "t.py::a": [finding({ count: 5 })],
      "t.py::b": [finding({ count: 9, line: 8, baselined: true })],
      "t.py::c": [finding({ label: "Order.items" })],
    }),
  );
  assert.equal(report.findings.length, 2);
  const merged = report.findings.find((f) => f.label === "Order.customer");
  assert.ok(merged);
  assert.equal(merged.count, 9);
  assert.equal(merged.total, 14);
  assert.equal(merged.testCount, 2);
  assert.equal(merged.line, 8);
  assert.equal(merged.baselined, false, "baselined only when every test's finding is");
  assert.deepEqual(merged.tests, ["t.py::a", "t.py::b"]);
  assert.equal(diagnosticMessage(merged), "Order.customer was lazy-loaded 5 times (in 2 tests)");
  assert.match(hoverMarkdown(merged), /14 times in 2 tests, up to 9 in one/);
});

test("findings without a file or line are skipped, an incomplete run is reported", () => {
  const report = parseReport(
    jsonReport({ "t.py::a": [finding({ file: "" }), finding({ line: 0 }), "junk"] }, { complete: false }),
  );
  assert.deepEqual(report.findings, []);
  assert.equal(report.complete, false);
});

test("an unknown format or a file that is not a report is refused", () => {
  assert.throws(() => parseReport("{"), ReportError);
  assert.throws(() => parseReport("[]"), ReportError);
  assert.throws(() => parseReport(JSON.stringify({ format: 2, tests: {} })), ReportError);
});

test("SARIF results of other tools and fixes that delete text are ignored", () => {
  const sarif = {
    version: "2.1.0",
    runs: [
      {
        results: [
          { ruleId: "other/x", locations: [{ physicalLocation: { artifactLocation: { uri: "a.py" }, region: { startLine: 1 } } }] },
          {
            ruleId: "queryguard/write-loop",
            level: "warning",
            message: { text: "Order rows were written one at a time 4 times" },
            properties: { count: 12, tests: 3, peakCount: 4, label: "Order" },
            locations: [{ physicalLocation: { artifactLocation: { uri: "a.py" }, region: { startLine: 3 } } }],
            fixes: [
              {
                artifactChanges: [
                  {
                    artifactLocation: { uri: "a.py" },
                    replacements: [
                      { deletedRegion: { startLine: 1, startColumn: 1, endLine: 1, endColumn: 4 }, insertedContent: { text: "x" } },
                    ],
                  },
                ],
              },
            ],
          },
        ],
      },
    ],
  };
  const [only, ...rest] = parseReport(JSON.stringify(sarif)).findings;
  assert.deepEqual(rest, []);
  assert.equal(only.kind, "write-loop");
  assert.equal(only.level, "warning");
  assert.equal(only.total, 12);
  assert.equal(only.count, 4);
  assert.equal(only.testCount, 3);
  assert.equal(only.fix, undefined);
  assert.equal(only.docs, docsUrl("write-loop"));
});

test("parseMessage reads indented continuation lines and nothing after", () => {
  const parts = parseMessage(
    [
      "headline",
      "first at a.py:1 in f",
      "  called from b.py:2 in g",
      "sql: SELECT 1",
      "fix: do this",
      "     and that",
      "kind: x  label: y",
      "  not part of the fix",
    ].join("\n"),
  );
  assert.deepEqual(parts, { headline: "headline", sql: "SELECT 1", hint: "do this\nand that", docs: undefined });
  assert.deepEqual(parseMessage("just text"), { headline: "just text", sql: undefined, hint: undefined, docs: undefined });
});

test("the docs link of an unknown kind is the page on failures", () => {
  assert.equal(docsUrl("new-kind"), "https://maciejziel.github.io/pytest-queryguard/when-a-test-fails.html");
});

test("the hover shows the message when it has no SQL or fix line, and escapes the headline", () => {
  const [f] = parseReport(jsonReport({ "t.py::a": [finding({ kind: "column-load", message: "Order.note *loaded* 5 times\nmore" })] })).findings;
  const md = hoverMarkdown(f);
  assert.match(md, /\\\*loaded\\\*/);
  assert.match(md, /```\nOrder.note \*loaded\* 5 times\nmore\n```/);
  assert.match(md, /\[Documentation\]\(https:\/\/maciejziel\.github\.io\/pytest-queryguard\/how-detection-works\.html#column-load\)/);
});

test("the docs table matches the plugin's", () => {
  // the extension lives in editors/vscode of the plugin's repository
  const findings = path.resolve(__dirname, "../../../../../src/pytest_queryguard/findings.py");
  const python = fs.readFileSync(findings, "utf8");
  const kinds = Object.fromEntries(
    [...python.matchAll(/^KIND_(\w+) = "([\w-]+)"$/gm)].map((m) => [m[1], m[2]]),
  );
  const table = /_KIND_DOCS = \{([\s\S]*?)\}/.exec(python)?.[1] ?? "";
  const entries = [...table.matchAll(/KIND_(\w+): "([^"]+)"/g)];
  assert.ok(entries.length >= 8);
  for (const [, name, page] of entries) {
    assert.equal(docsUrl(kinds[name]), `https://maciejziel.github.io/pytest-queryguard/${page}`, name);
  }
});

test("a merged SARIF message keeps the finding's own first line as the headline", () => {
  const parts = parseMessage(
    "Seen in 3 tests: 12 times in total, up to 4 times in one test. The test with the earliest line:\n" +
      "Order.customer was lazy-loaded 4 times\nsql: SELECT 1",
  );
  assert.equal(parts.headline, "Order.customer was lazy-loaded 4 times");
  assert.equal(parts.sql, "SELECT 1");
});

test("a merged JSON finding takes the message and callers of its earliest line", () => {
  const report = parseReport(
    jsonReport({
      "t.py::a": [finding({ line: 40, message: "at forty", stack: [{ file: "t.py", line: 5, function: "a" }] })],
      "t.py::b": [
        finding({
          line: 12,
          message: "at twelve",
          stack: [
            { file: "t.py", line: 9, function: "b" },
            { file: "t.py", line: null, function: "broken" },
          ],
        }),
      ],
    }),
  );
  const [merged] = report.findings;
  assert.equal(merged.line, 12);
  assert.equal(merged.headline, "at twelve");
  assert.deepEqual(merged.stack, [{ file: "t.py", line: 9, function: "b" }], "a frame without a line is dropped");
  assert.equal(merged.testCount, 2);
});
