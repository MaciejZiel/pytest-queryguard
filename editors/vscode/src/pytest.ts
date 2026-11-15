// Building the pytest command and finding files; no vscode import here.

import * as path from "node:path";

const HEADER = /^(\s*)(?:async\s+def|def|class)\s+(\w+)/;

/**
 * The names of the test at a 0-based line, outermost first (`["TestOrders",
 * "test_list"]`), or undefined when the line is in no test function. Walks up
 * from the line through the blocks that enclose it, by indentation.
 */
export function testAt(lines: readonly string[], line: number): string[] | undefined {
  // on a decorator: the function it decorates
  while (line + 1 < lines.length && /^\s*@/.test(lines[line])) line++;
  let limit = Infinity;
  const names: string[] = [];
  let test: string | undefined;
  for (let i = Math.min(line, lines.length - 1); i >= 0; i--) {
    const text = lines[i];
    if (!text.trim() || text.trimStart().startsWith("#")) continue;
    const indent = text.length - text.trimStart().length;
    if (indent >= limit) continue;
    limit = indent;
    const match = HEADER.exec(text);
    if (match) {
      const isClass = /^\s*class\s/.test(text);
      if (!test) {
        // the innermost function named test*, nested helpers aside
        if (!isClass && match[2].startsWith("test")) {
          test = match[2];
          names.unshift(test);
        }
      } else if (isClass) {
        names.unshift(match[2]);
      } else {
        // a test defined inside a function is not collected
        return undefined;
      }
    }
    if (indent === 0) break;
  }
  return test ? names : undefined;
}

/** A path relative to `cwd`, `/` separated, as pytest takes it. */
export function relativeForPytest(cwd: string, file: string): string {
  const rel = path.relative(cwd, file);
  return rel.split(path.sep).join("/");
}

/** `path::Class::test`, the node id pytest selects a test by. */
export function nodeId(cwd: string, file: string, names: readonly string[]): string {
  return [relativeForPytest(cwd, file), ...names].join("::");
}

/** The report option for a report path: SARIF when it ends in `.sarif`. */
export function reportOption(reportPath: string): string {
  const flag = reportPath.toLowerCase().endsWith(".sarif") ? "--queryguard-sarif" : "--queryguard-json";
  return `${flag}=${reportPath}`;
}

export function pytestArgs(reportPath: string, extra: readonly string[], targets: readonly string[]): string[] {
  return ["-m", "pytest", reportOption(reportPath), ...extra, ...targets];
}

/**
 * The absolute path of a file named in the report: tried against each base
 * directory in turn, the first one where it exists wins.
 */
export function resolveReportPath(
  file: string,
  bases: readonly string[],
  exists: (candidate: string) => boolean,
): string | undefined {
  if (path.isAbsolute(file)) return exists(file) ? file : undefined;
  for (const base of bases) {
    const candidate = path.resolve(base, file);
    if (exists(candidate)) return candidate;
  }
  return undefined;
}
