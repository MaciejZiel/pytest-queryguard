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
  parse them. Use `QueryLog` or the machine-readable outputs, once those
  exist, when a program needs the data.
- Which statements SQLAlchemy emits for a given ORM call; that depends on the
  SQLAlchemy version.
- The PostgreSQL index check beyond what the documentation promises: it is
  documented for PostgreSQL 16 and newer, and on older servers it only prints
  a notice.

## Version numbers

- Patch (`x.y.Z`): bug fixes and documentation. No new public API, no change
  to defaults. A fix can make a test report a finding it missed before;
  that is a fix, not a breaking change.
- Minor (`x.Y.0`): new public API, new options with a default that keeps
  today's behaviour, new finding kinds that are off or reported as warnings
  first, deprecations, and dropping support for an end-of-life Python,
  SQLAlchemy or pytest series.
- Major (`X.0.0`): removals of deprecated API, changed defaults, and
  incompatible changes to anything listed under "What is public".

While the version is below 1.0, a minor release may also contain breaking
changes. They are still deprecated first whenever that is possible, and are
always listed under "Changed" or "Removed" in the changelog.

## Deprecation policy

1. The old name or behaviour keeps working and starts emitting a
   `QueryGuardWarning` (a `UserWarning` subclass) that names the replacement
   and the release in which it will be removed. Command-line flags and ini
   options print the same warning once per run.
2. The deprecation is listed under "Deprecated" in the changelog, and the
   documentation points to the replacement.
3. It stays for at least one minor release (two, from 1.0 on) and at least
   three months after the release that deprecated it, whichever is longer.
4. It is removed in the next major release (before 1.0: in a minor release),
   listed under "Removed".

A security or data-loss fix may skip these steps; the changelog says so.

A new finding kind, or a stricter version of an existing check, first appears
in a release where it can be turned off with an ini option, and the changelog
says how.

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
