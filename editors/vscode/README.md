# pytest-queryguard for VS Code

Shows the findings of the last [pytest-queryguard](https://github.com/MaciejZiel/pytest-queryguard)
run on their lines: an N+1 query, a statement repeated in a loop, a column
loaded row by row. Each finding is a squiggle and an entry of the **Problems**
panel; its hover has the SQL, how many times it ran, the suggested fix and the
tests that found it, and its quick actions open the documentation of the
finding or, with a SARIF report, apply the fix.

The extension reads a report that pytest-queryguard writes; it does not detect
anything itself. Its commands run pytest on the current file or test and read
the new report.

## Requirements

- pytest-queryguard 0.8.0 or later in the project's environment.
- VS Code 1.90 or later.
- Optional: the [Python extension](https://marketplace.visualstudio.com/items?itemName=ms-python.python),
  whose selected interpreter then runs pytest.

## Use

1. Run your tests with the JSON report, from a terminal or a task:
   `python -m pytest --queryguard-json=queryguard.json`. Or run
   **queryguard: Run pytest on the Whole Project** from the command palette,
   which does the same.
2. Open a file with a finding: the line is marked. Hover it for the SQL, the
   count and the fix; **Quick Fix...** (Ctrl+. / Cmd+.) opens the
   documentation of the finding.
3. After a change, **queryguard: Run pytest on This File** or
   **queryguard: Run pytest on the Test at the Cursor** runs only that file or
   test and marks what it finds. The report then holds that run only: findings
   of other tests disappear until the next full run.

The marks follow the report file: when anything writes it (a run from the
terminal, a task, the commands above), they are replaced. A run that was
interrupted (a collection error, `-x`, Ctrl-C) writes a JSON report marked
incomplete, so the marks cover only the tests that ran, and no SARIF file, so
the marks of the previous run stay; the commands warn when pytest stopped
early, and the **queryguard** output channel says when a report is
incomplete.

With `"queryguard.reportFile": "queryguard.sarif"` the extension reads the
[SARIF file](https://maciejziel.github.io/pytest-queryguard/sarif.html)
instead and the commands write it with `--queryguard-sarif`. SARIF findings
have their level (error for a kind that fails tests) and, for a
`relationship-load` finding whose parent query is known, a quick fix that adds
the loader option and its import. The JSON report has no fixes, but it names
the tests and lists baselined findings.

## Settings

| Setting | Default | What |
|---|---|---|
| `queryguard.reportFile` | `queryguard.json` | The report, relative to `queryguard.pytestDirectory`. Ends in `.sarif`: a SARIF file. |
| `queryguard.pytestDirectory` | (the workspace folder) | Where pytest runs, relative to the workspace folder. Set it to pytest's rootdir (the directory of `pytest.ini` or `pyproject.toml`) when that is a subdirectory: the files in the report are relative to it. |
| `queryguard.pythonPath` | (the Python extension's interpreter, else `python3`) | The Python that runs `python -m pytest`. |
| `queryguard.pytestArgs` | `[]` | More pytest arguments, before the file or test. |
| `queryguard.showBaselined` | `false` | Also mark findings accepted by the [baseline](https://maciejziel.github.io/pytest-queryguard/baseline.html), as hints (JSON report only). |

## Limits

- The marks are where the findings were when the report was written. Edit the
  file and they stay on their line numbers until the next run.
- A finding whose file is not found under `queryguard.pytestDirectory`, the
  report's directory or the workspace folder is left out, and the
  **queryguard** output channel says how many were.
- The extension reads the text of a finding's message for the SQL and the fix.
  That text is not a stable interface of pytest-queryguard: if a later release
  words it differently, the hover shows the whole message instead.
- **Run pytest on the Test at the Cursor** finds the test by indentation, so a
  line of a multi-line string that starts at column 0 hides the test above it.
- The fix of a SARIF finding is offered only while the lines it edits are as
  they were when the report was read; run the tests again after editing them.

See [In your editor](https://maciejziel.github.io/pytest-queryguard/editor.html)
for the other ways to see findings in VS Code and PyCharm.

## Development

```console
cd editors/vscode
npm ci
npm test                    # compile and run the unit tests
npm run test:integration    # downloads VS Code; under Linux without a display: xvfb-run -a npm run test:integration
npm run package             # writes pytest-queryguard-<version>.vsix
```

To try a change, `npm run compile`, then
`code --extensionDevelopmentPath="$PWD" path/to/a/project` opens a window with
the extension from this directory. Install a `.vsix` with
`code --install-extension pytest-queryguard-<version>.vsix`.

The `vscode` workflow of the repository runs the unit tests, the integration
tests (in the oldest supported VS Code and the newest) and builds the `.vsix`
as the artifact `pytest-queryguard-vsix` on every pull request and push that
touches `editors/vscode/`.

## Publishing

The extension is in the [VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=maciejziel.pytest-queryguard);
it is not on Open VSX yet. Publishing needs the owner's accounts;
the steps, from a checkout of the commit to publish:

1. Set `version` in `package.json` and add a line to `CHANGELOG.md` of this
   directory; merge that, then take the `.vsix` from the `pytest-queryguard-vsix`
   artifact of the `vscode` workflow run of the merge commit (or build it with
   `npm ci && npm run package`). Tag the commit `vscode-v<version>`.
2. VS Code Marketplace, once: sign in at
   <https://marketplace.visualstudio.com/manage> with a Microsoft account and
   create a publisher whose ID is `maciejziel` (the `publisher` field of
   `package.json`; change the field if the ID is taken). Then create a
   personal access token in Azure DevOps (<https://dev.azure.com>, **User
   settings > Personal access tokens**): Organization **All accessible
   organizations**, scope **Marketplace > Manage**.
3. Publish to the Marketplace:

   ```console
   cd editors/vscode
   npm ci
   npx vsce login maciejziel          # paste the token
   npx vsce publish --packagePath pytest-queryguard-<version>.vsix
   ```

   or, without `login`, `VSCE_PAT=<token> npx vsce publish --packagePath ...`.
   The listing appears at
   <https://marketplace.visualstudio.com/items?itemName=maciejziel.pytest-queryguard>
   after the Marketplace's checks, usually within minutes.
4. Open VSX (VSCodium, Cursor and other editors), once: sign in at
   <https://open-vsx.org> with GitHub, sign the Eclipse Foundation Open VSX
   Publisher Agreement in the profile settings, create an access token, and
   create the namespace: `npx ovsx create-namespace maciejziel -p <token>`.
   Then for each version:

   ```console
   npx ovsx publish pytest-queryguard-<version>.vsix -p <token>
   ```

5. Link the listing from `docs/editor.md` of the repository.

Keep the tokens out of the repository. To publish from CI instead, store them
as the repository secrets `VSCE_PAT` and `OVSX_PAT` and add a job that runs
the two publish commands on a `vscode-v*` tag.
