# Changelog

Changes of the VS Code extension. The plugin's own changes are in the
[CHANGELOG](https://github.com/MaciejZiel/pytest-queryguard/blob/main/CHANGELOG.md)
of the repository.

## 0.1.0 (unreleased)

- First version: marks the findings of a `--queryguard-json` or
  `--queryguard-sarif` report on their lines and reloads it when it changes;
  the hover shows the SQL, the count, the fix and the tests; quick actions
  open the documentation and apply a SARIF fix; commands run pytest on the
  current file, the test at the cursor or the whole project.
