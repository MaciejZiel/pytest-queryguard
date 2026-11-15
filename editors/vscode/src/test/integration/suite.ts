// Runs inside VS Code with test-fixtures/workspace open (see runTests.ts).
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";

const folder = () => vscode.workspace.workspaceFolders![0].uri.fsPath;
const file = (rel: string) => vscode.Uri.file(path.join(folder(), rel));

async function until<T>(what: string, get: () => T | undefined, timeoutMs = 15000): Promise<T> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const value = get();
    if (value !== undefined) return value;
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${what}`);
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
}

const ours = (uri: vscode.Uri) => vscode.languages.getDiagnostics(uri).filter((d) => d.source === "queryguard");

function diagnosticsOn(uri: vscode.Uri, check: (d: vscode.Diagnostic[]) => boolean) {
  return until(`diagnostics on ${uri.fsPath}`, () => {
    const found = ours(uri);
    return check(found) ? found : undefined;
  });
}

async function setReport(value: string | undefined): Promise<void> {
  await vscode.workspace
    .getConfiguration("queryguard")
    .update("reportFile", value, vscode.ConfigurationTarget.Workspace);
}

const tests: [string, () => Promise<void>][] = [];
const it = (name: string, fn: () => Promise<void>) => tests.push([name, fn]);

it("marks the findings of the JSON report on their lines", async () => {
  const orders = file("app/orders.py");
  const [d] = await diagnosticsOn(orders, (d) => d.length === 1);
  assert.equal(d.range.start.line, 9);
  assert.equal(d.range.start.character, 8, "starts after the indentation");
  assert.equal(d.message, "Order.customer was lazy-loaded 50 times");
  assert.equal(d.severity, vscode.DiagnosticSeverity.Warning);
  const code = d.code as { value: string; target: vscode.Uri };
  assert.equal(code.value, "relationship-load");
  assert.equal(
    code.target.toString(true),
    "https://maciejziel.github.io/pytest-queryguard/how-detection-works.html#relationship-load",
  );
  assert.equal(d.relatedInformation?.[0].location.uri.fsPath, file("tests/test_orders.py").fsPath);

  const [r] = await diagnosticsOn(file("app/customers.py"), (d) => d.length === 1);
  assert.equal(r.message, "the same statement ran 50 times");
});

it("shows the SQL, the count and the fix on hover", async () => {
  const orders = file("app/orders.py");
  await vscode.workspace.openTextDocument(orders);
  const hovers = await vscode.commands.executeCommand<vscode.Hover[]>(
    "vscode.executeHoverProvider",
    orders,
    new vscode.Position(9, 20),
  );
  const text = hovers
    .flatMap((h) => h.contents)
    .map((c) => (typeof c === "string" ? c : c.value))
    .join("\n");
  assert.match(text, /SELECT customer\.id, customer\.name FROM customer WHERE customer\.id = \?/);
  assert.match(text, /50 times/);
  assert.match(text, /joinedload\(Order\.customer\)/);
  assert.match(text, /tests\/test_orders\.py::test_list_orders/);
});

it("offers to open the documentation", async () => {
  const actions = await vscode.commands.executeCommand<vscode.CodeAction[]>(
    "vscode.executeCodeActionProvider",
    file("app/orders.py"),
    new vscode.Range(9, 0, 9, 0),
  );
  const docs = actions.find((a) => a.title === "Open queryguard docs: relationship-load");
  assert.ok(docs, `actions: ${actions.map((a) => a.title).join(", ")}`);
  // VS Code hands the command of a provided action back as its internal form
  assert.ok(["vscode.open", "_workbench.open"].includes(docs.command?.command ?? ""), docs.command?.command);
});

it("reads a SARIF report and applies its fix", async () => {
  await setReport("queryguard.sarif");
  const orders = file("app/orders.py");
  const [d] = await diagnosticsOn(orders, (all) => all.length === 1 && all[0].severity === vscode.DiagnosticSeverity.Error);
  assert.equal(d.range.start.line, 9);
  const actions = await vscode.commands.executeCommand<vscode.CodeAction[]>(
    "vscode.executeCodeActionProvider",
    orders,
    new vscode.Range(9, 0, 9, 0),
  );
  const fix = actions.find((a) => a.title.startsWith("Load Order.customer with the parent query"));
  assert.ok(fix?.edit, `actions: ${actions.map((a) => a.title).join(", ")}`);
  assert.ok(await vscode.workspace.applyEdit(fix.edit));
  const document = await vscode.workspace.openTextDocument(orders);
  try {
    assert.equal(document.lineAt(1).text, "from sqlalchemy.orm import Session, joinedload");
    assert.equal(
      document.lineAt(8).text,
      "    for order in session.scalars(select(Order).options(joinedload(Order.customer))).all():",
    );
    // the lines have changed: the fix is not offered again
    const after = await vscode.commands.executeCommand<vscode.CodeAction[]>(
      "vscode.executeCodeActionProvider",
      orders,
      new vscode.Range(9, 0, 9, 0),
    );
    assert.ok(!after.some((a) => a.title === fix.title), `actions: ${after.map((a) => a.title).join(", ")}`);
  } finally {
    await vscode.window.showTextDocument(document);
    await vscode.commands.executeCommand("workbench.action.files.revert");
  }
});

it("reloads when the report changes", async () => {
  const report = path.join(folder(), "changed-report.json");
  await setReport("changed-report.json");
  try {
    await diagnosticsOn(file("app/orders.py"), (d) => d.length === 0);
    const json = fs.readFileSync(path.join(folder(), "queryguard.json"), "utf8");
    fs.writeFileSync(report, json.replace(/lazy-loaded 50 times/g, "lazy-loaded 7 times"));
    const [d] = await diagnosticsOn(file("app/orders.py"), (d) => d.length === 1);
    assert.equal(d.message, "Order.customer was lazy-loaded 7 times");
    fs.rmSync(report);
    await diagnosticsOn(file("app/orders.py"), (d) => d.length === 0);
  } finally {
    fs.rmSync(report, { force: true });
    await setReport(undefined);
  }
});

it("clears the findings on request", async () => {
  await diagnosticsOn(file("app/orders.py"), (d) => d.length === 1);
  await vscode.commands.executeCommand("queryguard.clear");
  await diagnosticsOn(file("app/orders.py"), (d) => d.length === 0);
  await vscode.commands.executeCommand("queryguard.refresh");
  await diagnosticsOn(file("app/orders.py"), (d) => d.length === 1);
});

export async function run(): Promise<void> {
  // the extension activates on Python files
  await vscode.workspace.openTextDocument(file("app/orders.py"));
  let failed = 0;
  try {
    for (const [name, fn] of tests) {
      try {
        await fn();
        console.log(`ok - ${name}`);
      } catch (error) {
        failed++;
        console.error(`not ok - ${name}\n`, error);
      }
    }
  } finally {
    fs.rmSync(path.join(folder(), ".vscode"), { recursive: true, force: true });
  }
  if (failed) throw new Error(`${failed} of ${tests.length} integration tests failed`);
}
