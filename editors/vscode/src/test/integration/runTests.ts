// Starts a VS Code (downloaded into .vscode-test) with the extension and the
// fixture workspace, and runs ./suite in it. CI runs it under xvfb.
import * as path from "node:path";
import { runTests } from "@vscode/test-electron";

async function main(): Promise<void> {
  const extensionDevelopmentPath = path.resolve(__dirname, "../../..");
  const workspace = path.join(extensionDevelopmentPath, "test-fixtures", "workspace");
  await runTests({
    version: process.env.VSCODE_VERSION || "stable",
    extensionDevelopmentPath,
    extensionTestsPath: path.resolve(__dirname, "suite"),
    launchArgs: [workspace, "--disable-extensions", "--disable-workspace-trust"],
  });
}

main().catch((error: unknown) => {
  console.error(error);
  process.exit(1);
});
