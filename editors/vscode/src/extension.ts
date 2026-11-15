import { spawn } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";
import {
  diagnosticMessage,
  Finding,
  hoverMarkdown,
  Level,
  parseReport,
  ReportError,
} from "./report";
import { nodeId, pytestArgs, relativeForPytest, resolveReportPath, testAt } from "./pytest";

const SOURCE = "queryguard";

interface Located {
  finding: Finding;
  uri: vscode.Uri;
  range: vscode.Range;
}

interface FolderSettings {
  folder: vscode.WorkspaceFolder;
  cwd: string;
  reportPath: string;
  pythonPath: string;
  pytestArgs: string[];
  showBaselined: boolean;
}

function settings(folder: vscode.WorkspaceFolder): FolderSettings {
  const config = vscode.workspace.getConfiguration("queryguard", folder.uri);
  const cwd = path.resolve(folder.uri.fsPath, config.get<string>("pytestDirectory", "") || ".");
  return {
    folder,
    cwd,
    reportPath: path.resolve(cwd, config.get<string>("reportFile", "") || "queryguard.json"),
    pythonPath: config.get<string>("pythonPath", ""),
    pytestArgs: config.get<string[]>("pytestArgs", []),
    showBaselined: config.get<boolean>("showBaselined", false),
  };
}

function severity(level: Level, baselined: boolean): vscode.DiagnosticSeverity {
  if (baselined) return vscode.DiagnosticSeverity.Hint;
  if (level === "error") return vscode.DiagnosticSeverity.Error;
  if (level === "note") return vscode.DiagnosticSeverity.Information;
  return vscode.DiagnosticSeverity.Warning;
}

/** The line of a file without its indentation; the whole line when unread. */
function lineRange(lines: string[] | undefined, line: number): vscode.Range {
  const text = lines?.[line];
  if (text === undefined) return new vscode.Range(line, 0, line, Number.MAX_SAFE_INTEGER);
  const start = text.length - text.trimStart().length;
  return new vscode.Range(line, start, line, Math.max(text.trimEnd().length, start));
}

class Findings implements vscode.HoverProvider, vscode.CodeActionProvider {
  readonly diagnostics = vscode.languages.createDiagnosticCollection(SOURCE);
  private located = new Map<string, Located[]>();

  constructor(private readonly output: vscode.OutputChannel) {}

  clear(): void {
    this.diagnostics.clear();
    this.located.clear();
  }

  /** Reads the report of every workspace folder and replaces the diagnostics. */
  reload(): void {
    this.clear();
    const perFile = new Map<string, { uri: vscode.Uri; items: vscode.Diagnostic[] }>();
    for (const folder of vscode.workspace.workspaceFolders ?? []) {
      const s = settings(folder);
      let text: string;
      try {
        text = fs.readFileSync(s.reportPath, "utf8");
      } catch {
        continue; // no report yet
      }
      let report;
      try {
        report = parseReport(text);
      } catch (error) {
        if (!(error instanceof ReportError)) throw error;
        this.output.appendLine(`${s.reportPath}: ${error.message}`);
        continue;
      }
      if (!report.complete) {
        this.output.appendLine(`${s.reportPath}: the run stopped early, some tests are missing`);
      }
      const bases = [s.cwd, path.dirname(s.reportPath), folder.uri.fsPath];
      const fileLines = new Map<string, string[] | undefined>();
      let unresolved = 0;
      for (const finding of report.findings) {
        if (finding.baselined && !s.showBaselined) continue;
        const file = resolveReportPath(finding.file, bases, fs.existsSync);
        if (!file) {
          unresolved++;
          continue;
        }
        if (!fileLines.has(file)) {
          try {
            fileLines.set(file, fs.readFileSync(file, "utf8").split(/\r?\n/));
          } catch {
            fileLines.set(file, undefined);
          }
        }
        const uri = vscode.Uri.file(file);
        const range = lineRange(fileLines.get(file), finding.line - 1);
        const diagnostic = new vscode.Diagnostic(
          range,
          diagnosticMessage(finding),
          severity(finding.level, finding.baselined),
        );
        diagnostic.source = SOURCE;
        diagnostic.code = { value: finding.kind, target: vscode.Uri.parse(finding.docs) };
        diagnostic.relatedInformation = finding.stack.flatMap((frame) => {
          const caller = resolveReportPath(frame.file, bases, fs.existsSync);
          return caller
            ? [
                new vscode.DiagnosticRelatedInformation(
                  new vscode.Location(vscode.Uri.file(caller), new vscode.Position(frame.line - 1, 0)),
                  `called from ${frame.function}`,
                ),
              ]
            : [];
        });
        const key = uri.toString();
        const entry = perFile.get(key) ?? { uri, items: [] };
        entry.items.push(diagnostic);
        perFile.set(key, entry);
        const located = this.located.get(key) ?? [];
        located.push({ finding, uri, range });
        this.located.set(key, located);
      }
      if (unresolved) {
        this.output.appendLine(
          `${s.reportPath}: ${unresolved} finding(s) name files not found under ${s.cwd}; ` +
            "set queryguard.pytestDirectory to pytest's rootdir",
        );
      }
    }
    for (const { uri, items } of perFile.values()) {
      this.diagnostics.set(uri, items);
    }
  }

  private at(document: vscode.TextDocument, line: number): Located[] {
    return (this.located.get(document.uri.toString()) ?? []).filter((l) => l.range.start.line === line);
  }

  provideHover(document: vscode.TextDocument, position: vscode.Position): vscode.Hover | undefined {
    const here = this.at(document, position.line);
    if (!here.length) return undefined;
    const contents = here.map((l) => new vscode.MarkdownString(hoverMarkdown(l.finding)));
    return new vscode.Hover(contents, here[0].range);
  }

  provideCodeActions(document: vscode.TextDocument, range: vscode.Range | vscode.Selection): vscode.CodeAction[] {
    const actions: vscode.CodeAction[] = [];
    const seen = new Set<string>();
    for (let line = range.start.line; line <= range.end.line; line++) {
      for (const { finding, uri } of this.at(document, line)) {
        const diagnostic = this.diagnostics
          .get(uri)
          ?.find((d) => d.range.start.line === line && (d.code as { value: string }).value === finding.kind);
        if (finding.fix) {
          const fixAction = new vscode.CodeAction(finding.fix.description, vscode.CodeActionKind.QuickFix);
          fixAction.edit = this.fixEdit(uri, finding);
          fixAction.isPreferred = true;
          if (diagnostic) fixAction.diagnostics = [diagnostic];
          actions.push(fixAction);
        }
        if (seen.has(finding.docs)) continue;
        seen.add(finding.docs);
        const docs = new vscode.CodeAction(`Open queryguard docs: ${finding.kind}`, vscode.CodeActionKind.QuickFix);
        docs.command = { command: "vscode.open", title: docs.title, arguments: [vscode.Uri.parse(finding.docs)] };
        if (diagnostic) docs.diagnostics = [diagnostic];
        actions.push(docs);
      }
    }
    return actions;
  }

  private fixEdit(uri: vscode.Uri, finding: Finding): vscode.WorkspaceEdit {
    const edit = new vscode.WorkspaceEdit();
    const folder = vscode.workspace.getWorkspaceFolder(uri);
    const s = folder ? settings(folder) : undefined;
    const bases = s ? [s.cwd, path.dirname(s.reportPath), s.folder.uri.fsPath] : [];
    for (const insertion of finding.fix?.insertions ?? []) {
      const file = resolveReportPath(insertion.file, bases, fs.existsSync);
      const target = file ? vscode.Uri.file(file) : uri;
      edit.insert(target, new vscode.Position(insertion.line - 1, insertion.column - 1), insertion.text);
    }
    return edit;
  }
}

async function pythonFor(s: FolderSettings): Promise<string> {
  if (s.pythonPath) return s.pythonPath;
  if (vscode.extensions.getExtension("ms-python.python")) {
    try {
      const interpreter = await vscode.commands.executeCommand<string>("python.interpreterPath", {
        workspaceFolder: s.folder.uri.fsPath,
      });
      if (interpreter) return interpreter;
    } catch {
      // the Python extension is not ready or has no interpreter selected
    }
  }
  return process.platform === "win32" ? "python" : "python3";
}

type Scope = "file" | "test" | "all";

async function run(scope: Scope, findings: Findings, output: vscode.OutputChannel): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  let folder: vscode.WorkspaceFolder | undefined;
  if (editor && editor.document.uri.scheme === "file") {
    folder = vscode.workspace.getWorkspaceFolder(editor.document.uri);
  }
  folder ??= vscode.workspace.workspaceFolders?.[0];
  if (!folder) {
    void vscode.window.showErrorMessage("queryguard: open a folder first.");
    return;
  }
  const s = settings(folder);
  const targets: string[] = [];
  if (scope !== "all") {
    if (!editor || editor.document.languageId !== "python" || editor.document.uri.scheme !== "file") {
      void vscode.window.showErrorMessage("queryguard: open a Python test file first.");
      return;
    }
    if (editor.document.isDirty) await editor.document.save();
    const file = editor.document.uri.fsPath;
    if (scope === "test") {
      const lines = editor.document.getText().split(/\r?\n/);
      const names = testAt(lines, editor.selection.active.line);
      if (!names) {
        void vscode.window.showErrorMessage("queryguard: the cursor is not in a test function.");
        return;
      }
      targets.push(nodeId(s.cwd, file, names));
    } else {
      targets.push(relativeForPytest(s.cwd, file));
    }
  }
  const python = await pythonFor(s);
  const args = pytestArgs(s.reportPath, s.pytestArgs, targets);
  output.appendLine(`$ cd ${s.cwd}`);
  output.appendLine(`$ ${[python, ...args].join(" ")}`);
  const code = await vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      title: `queryguard: running pytest${targets.length ? ` on ${targets[0]}` : ""}`,
      cancellable: true,
    },
    (_progress, token) =>
      new Promise<number | null>((resolve) => {
        const child = spawn(python, args, { cwd: s.cwd, env: process.env });
        child.stdout.on("data", (chunk: Buffer) => output.append(chunk.toString()));
        child.stderr.on("data", (chunk: Buffer) => output.append(chunk.toString()));
        token.onCancellationRequested(() => child.kill());
        child.on("error", (error) => {
          output.appendLine(`could not start ${python}: ${error.message}`);
          resolve(null);
        });
        child.on("close", (exitCode) => resolve(exitCode));
      }),
  );
  findings.reload();
  // 0: passed, 1: tests failed, 5: no tests collected; the rest wrote no report
  if (code !== 0 && code !== 1 && code !== 5) {
    output.show(true);
    void vscode.window.showWarningMessage(
      `queryguard: pytest exited with ${code ?? "an error"}; the report was not updated. See the output.`,
    );
  }
}

export function activate(context: vscode.ExtensionContext): void {
  const output = vscode.window.createOutputChannel("queryguard");
  const findings = new Findings(output);
  context.subscriptions.push(output, findings.diagnostics);

  const selector: vscode.DocumentSelector = { scheme: "file", language: "python" };
  context.subscriptions.push(
    vscode.languages.registerHoverProvider(selector, findings),
    vscode.languages.registerCodeActionsProvider(selector, findings, {
      providedCodeActionKinds: [vscode.CodeActionKind.QuickFix],
    }),
    vscode.commands.registerCommand("queryguard.runFile", () => run("file", findings, output)),
    vscode.commands.registerCommand("queryguard.runTest", () => run("test", findings, output)),
    vscode.commands.registerCommand("queryguard.runAll", () => run("all", findings, output)),
    vscode.commands.registerCommand("queryguard.refresh", () => findings.reload()),
    vscode.commands.registerCommand("queryguard.clear", () => findings.clear()),
  );

  // Reload when a report changes, wherever the settings put it.
  let watchers: vscode.Disposable[] = [];
  const watch = () => {
    watchers.forEach((w) => w.dispose());
    watchers = [];
    for (const folder of vscode.workspace.workspaceFolders ?? []) {
      const { reportPath } = settings(folder);
      const watcher = vscode.workspace.createFileSystemWatcher(
        new vscode.RelativePattern(vscode.Uri.file(path.dirname(reportPath)), path.basename(reportPath)),
      );
      const reload = () => findings.reload();
      watcher.onDidCreate(reload);
      watcher.onDidChange(reload);
      watcher.onDidDelete(reload);
      watchers.push(watcher);
    }
  };
  watch();
  context.subscriptions.push(
    { dispose: () => watchers.forEach((w) => w.dispose()) },
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration("queryguard")) {
        watch();
        findings.reload();
      }
    }),
    vscode.workspace.onDidChangeWorkspaceFolders(() => {
      watch();
      findings.reload();
    }),
  );
  findings.reload();
}

export function deactivate(): void {}
