# Security policy

## Supported versions

From 1.0.0 on, security fixes go into a patch release of the latest 1.x
minor release and into `main`. Older minor releases do not get backports, and
the 0.x releases get no fixes at all: upgrade to the latest 1.x release to
receive one. The [stability policy](docs/stability.md#which-releases-get-fixes)
keeps such an upgrade compatible within 1.x.

| Version | Security fixes |
|---|---|
| latest 1.x minor release | yes |
| older 1.x minor releases | no, upgrade to the latest 1.x |
| 0.x | no |

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
baseline or configuration file, and the GitHub Action: `action/` in this
repository and its copy for the GitHub Marketplace in
[`MaciejZiel/pytest-queryguard-action`](https://github.com/MaciejZiel/pytest-queryguard-action),
which is generated from `action/` (report a problem in either here). This is a
single-maintainer project, so replies are best effort.

A finding that the plugin misses or reports wrongly is a normal bug: use the
[issue form](https://github.com/MaciejZiel/pytest-queryguard/issues/new/choose).
