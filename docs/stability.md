# Stability and versioning policy

pytest-queryguard follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
This page says what that covers, how deprecations work, and which tests
enforce it.

## What is public

The public API is everything below. Changing it incompatibly needs a major
release (see "Version numbers"); `tests/test_public_api.py` fails when it
changes by accident.

- The names in `pytest_queryguard.__all__`, their signatures, and the public
  attributes and methods of the classes in it (`QueryLog`, `Query`,
  `CallSite`, the exceptions and the warning classes). A new keyword argument
  with a default, or a new attribute, is not a breaking change.
- The pytest integration: the ini options (`queryguard_*`), the command-line
  flags (`--queryguard-*`, `--no-queryguard-*`), their defaults and accepted
  values, and the markers (`max_queries`, `max_db_time`, `allow_nplusone`,
  `allow_missing_index`).
- The meaning of the finding kinds and of the labels and patterns used by
  `queryguard_ignore` and `allow_nplusone`, so that an existing ignore keeps
  matching the same finding.
- The pytest entry point name (`queryguard`).
- Supported versions: Python, SQLAlchemy and pytest releases listed in
  `pyproject.toml` and the CI matrix. Dropping a Python, SQLAlchemy or pytest
  series is announced in the changelog and happens in a minor release at the
  earliest after the series is end of life upstream.

## What is not public

- Everything in a module whose name starts with an underscore, and any name
  not exported from `pytest_queryguard`. The submodules (`collector`,
  `listeners`, `detectors`, `indexes`, `messages`, `plugin`, ...) are
  implementation details even though they can be imported.
- The wording and layout of failure messages, warnings, summaries and
  reports. They are written for people and improve between releases; do not
  parse them. Use `QueryLog`, the SARIF file or the baseline file when a program needs
  the data.
- Which statements SQLAlchemy emits for a given ORM call; that depends on the
  SQLAlchemy version.
- The index check beyond what the documentation promises: it is documented
  for PostgreSQL 16 and newer, SQLite, MySQL and MariaDB, and on older PostgreSQL servers
  it only prints a notice. It is tested on PostgreSQL 16 to 18; on a newer
  server it runs and prints a notice that the version is untested.

## Version numbers

- Patch (`x.y.Z`): bug fixes and documentation. No new public API, no change
  to defaults. A fix can make a test report a finding it missed before;
  that is a fix, not a breaking change.
- Minor (`x.Y.0`): new public API, new options with a default that keeps
  today's behaviour, new finding kinds that are off or reported as warnings
  first (from 1.0 on; see below for 0.x), deprecations, and dropping support for an end-of-life Python,
  SQLAlchemy or pytest series.
- Major (`X.0.0`): removals of deprecated API, changed defaults, and
  incompatible changes to anything listed under "What is public".

While the version is below 1.0, a minor release may also contain breaking
changes. They are still deprecated first whenever that is possible, and are
always listed under "Changed" or "Removed" in the changelog.

**Pre-1.0 clause for new checks.** While the version is 0.x, a minor release
may change the defaults of newly added detectors: switch a new check on, or let
it fail tests by default, instead of introducing it off or as a warning first.
Every such change is named in the introduction at the top of the changelog
entry and listed first under "Changed", in a "Behavior changes" group, with the
option that turns it off. The
0.4.0rc1 changelog entry is an example: the `column-load` kind is on by default, the index
advisor is on whenever the index check is on, and the index check fails tests
on SQLite. From 1.0 on, the rules above apply strictly: a new check is off or
only warns until a later minor release, and a change of default needs a major
release.

## Deprecation policy

1. The old name or behaviour keeps working and starts emitting a
   `QueryGuardDeprecationWarning` (a `DeprecationWarning` subclass, exported
   from `pytest_queryguard`) that names the replacement and the release in
   which it will be removed. It is not a `QueryGuardWarning`, so a
   `filterwarnings` entry that turns findings into errors, or ignores them,
   does not catch it; pytest shows `DeprecationWarning` in the warnings
   summary by default. Command-line flags and ini options print the same
   warning once per run.
2. The deprecation is listed under "Deprecated" in the changelog, and the
   documentation points to the replacement.
3. It stays for at least one minor release (two, from 1.0 on) and at least
   three months after the release that deprecated it, whichever is longer.
4. It is removed in the next major release (before 1.0: in a minor release),
   listed under "Removed".

A security or data-loss fix may skip these steps; the changelog says so.

A new finding kind, or a stricter version of an existing check, first appears
in a release where it can be turned off with an ini option, and the changelog
says how. Before 1.0 it may be on by default (see the pre-1.0 clause above).

## Experimental

Options, finding kinds and markers listed here are public to use but exempt
from the deprecation policy: a minor release may change their values,
defaults, labels or messages, or remove them, with a changelog entry under
"Changed" or "Removed" and no deprecation period. The ini option is marked
`EXPERIMENTAL` in `pytest --help`.

- `queryguard_unused_eager_load` and the `unused-eager-load` finding kind
  (rule `queryguard/unused-eager-load` in SARIF). See
  [Unused eager loads](unused-eager-load.md).
- The missing-index check on MySQL and MariaDB (tested by hand, not in CI; see
  [the MySQL and MariaDB page](index-check-mysql.md)).

Everything else listed under "What is public" follows the deprecation policy.
`tests/test_public_api.py` still snapshots the experimental ini option and its
default, so a change to it is deliberate and visible in review.

## How this is enforced

- `tests/test_public_api.py` snapshots `__all__`, signatures, dataclass
  fields, ini option names and defaults, command-line flags, markers and the
  entry point. A failing snapshot means: the change is breaking, or it is an
  addition that must be added to the snapshot on purpose. Update the snapshot
  in the same commit as the change, so reviewers see it.
- CI runs the suite on Python 3.10 to 3.14, SQLAlchemy 2.0 and the latest
  release, the oldest and the latest pytest, PostgreSQL 14 to 18, Windows and
  macOS, and under pytest-xdist. A nightly job tries the SQLAlchemy
  development branch, pytest pre-releases and Python 3.15; it is allowed to
  fail and tells maintainers about upcoming incompatibilities early.
