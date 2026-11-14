# In your editor

Since 0.10.0.

pytest-queryguard has no editor extension. Its output is regular enough for
the tools editors already have: a problem matcher turns the test run and the
[static check](static-check.md) into entries of VS Code's Problems panel,
and its [SARIF file](sarif.md) opens in a SARIF viewer. Nothing here is
specific to a release of pytest-queryguard beyond the lines it reads, which
are not a stable interface (see [Stability](stability.md#what-is-not-public)):
if a matcher stops matching after an upgrade, check this page.

## VS Code: tasks with problem matchers

Save as `.vscode/tasks.json` (or merge the two tasks into yours), then run
**Terminal > Run Task...** and pick one. Each finding becomes an entry of the
**Problems** panel and a squiggle on its line.

```json
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "pytest (queryguard)",
      "type": "process",
      "command": "${command:python.interpreterPath}",
      "args": ["-m", "pytest", "-q"],
      "group": "test",
      "problemMatcher": {
        "owner": "queryguard",
        "source": "queryguard",
        "fileLocation": ["autoDetect", "${workspaceFolder}"],
        "severity": "error",
        "pattern": [
          {"regexp": "^  (\\S.*)$", "message": 1},
          {"regexp": "^  first at (.+):(\\d+) in (.+)$", "file": 1, "line": 2}
        ]
      }
    },
    {
      "label": "queryguard lint",
      "type": "process",
      "command": "${command:python.interpreterPath}",
      "args": ["-m", "pytest_queryguard.lint", "--models", "app.models", "app"],
      "problemMatcher": {
        "owner": "queryguard-lint",
        "source": "queryguard lint",
        "fileLocation": ["autoDetect", "${workspaceFolder}"],
        "severity": "warning",
        "pattern": {
          "regexp": "^(.+?):(\\d+):(\\d+): (.+)$",
          "file": 1,
          "line": 2,
          "column": 3,
          "message": 4
        }
      }
    }
  ]
}
```

- `${command:python.interpreterPath}` is the interpreter selected in the
  Python extension; write the path of your virtual environment's `python`
  without that extension.
- The pytest matcher reads the two lines of a finding that say what repeated
  and where: `Order.customer was lazy-loaded 50 times` and
  `first at app/orders.py:10 in list_orders`. That covers every finding with
  a source line (N+1, repeated statements, column loads, write loops); a
  budget failure (`expected at most 2 queries, got 4`) has no such line and
  stays in the terminal only.
- The paths of the test report are relative to pytest's rootdir, those of
  the static check to its working directory. With a `pytest.ini` or
  `pyproject.toml` in a subdirectory, put that directory in `fileLocation`:
  `["autoDetect", "${workspaceFolder}/backend"]`, and set `"options":
  {"cwd": "${workspaceFolder}/backend"}` on the task.
- Change `--models` and the paths of the lint task to yours (see
  [Static check](static-check.md#running-it)).
- Both commands exit with 1 when they report something, so VS Code shows the
  task as failed: that is expected.

## VS Code: the SARIF file

Microsoft's [SARIF Viewer](https://marketplace.visualstudio.com/items?itemName=MS-SarifVSCode.sarif-viewer)
extension (`MS-SarifVSCode.sarif-viewer`) shows a SARIF file as a list of
results, with the location, the message, the rule's help and, for the
plugin's relationship-load findings, the fix it carries:

1. Write the file: `python -m pytest --queryguard-sarif=queryguard.sarif`,
   or for the static check
   `python -m pytest_queryguard.lint --models app.models --format sarif --output lint.sarif app`.
2. Open it in VS Code (**File > Open File...**): the extension opens its
   panel for files ending in `.sarif`. **SARIF: Show Panel** from the
   command palette opens the panel again.

The locations in the file are relative to the repository root when it was
written in GitHub Actions, and to pytest's rootdir otherwise (see
[SARIF output](sarif.md#paths-and-the-repository-root)); open the file from
the folder they are relative to, or the viewer asks you to find the files.

## PyCharm and other JetBrains IDEs

The cheap path is narrower here:

- The run console of a pytest run configuration shows the report as it is;
  the `first at` lines are not turned into links by pytest-queryguard.
- The static check can be an External Tool (**Settings > Tools > External
  Tools**): Program the project's `python`, Arguments
  `-m pytest_queryguard.lint --models app.models $FilePathRelativeToProjectRoot$`,
  Working directory `$ProjectFileDir$`. It then runs on the current file
  from the **Tools > External Tools** menu or a keyboard shortcut. Its output
  filters (`$FILE_PATH$:$LINE$:$COLUMN$.*`) make links only from absolute
  paths, per JetBrains' documentation, and the check prints paths relative
  to the working directory, so the output is plain text.
- JetBrains IDEs show SARIF files through their Qodana integration, which
  is built for Qodana's own reports; it has not been tried with these files.

An editor extension that shows the findings of the last test run on their
lines is tracked in
[#249](https://github.com/MaciejZiel/pytest-queryguard/issues/249).
