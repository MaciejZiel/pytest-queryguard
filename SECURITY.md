# Security policy

## Supported versions

Security fixes go into the latest release on PyPI and into `main`. Older
releases do not get backports. The project is pre-1.0, so upgrading to the
latest release is the way to receive a fix.

## Reporting a vulnerability

Please report it privately and do not open a public issue:

1. Open <https://github.com/MaciejZiel/pytest-queryguard/security/advisories/new>
   (the "Security" tab of the repository, then "Report a vulnerability").
2. Describe what you found, the version of pytest-queryguard, and steps or a
   small project that reproduces it.

A fix is prepared in the
private advisory, released, and then the advisory is published with credit to
you if you want it. Please keep the details private until then.

## What counts

pytest-queryguard runs inside a test process and reads the SQL that process
sends. Examples that are in scope: reading or writing files outside the paths
a user configured (baseline and SARIF output), code execution from a crafted
baseline or configuration file, and the `action/` GitHub Action. This is a
single-maintainer project, so replies are best effort.

A finding that the plugin misses or reports wrongly is a normal bug: use the
[issue form](https://github.com/MaciejZiel/pytest-queryguard/issues/new/choose).
