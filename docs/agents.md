# Coding agents

Since 0.10.0.

A coding agent writes data-access code fast, and an N+1 query passes every
test that only checks results. pytest-queryguard fails those tests, for
SQLAlchemy 2 and for the Django ORM ([experimental](django.md)); the files
on this page tell an agent (Claude Code, Cursor, Codex, Copilot and other
tools that read `AGENTS.md`) to run them, read the findings as data, apply the
suggested loader option (`joinedload`/`selectinload`, or
`select_related`/`prefetch_related` in Django) and run the tests again to
check the fix. They are short on purpose: the agent learns the loop, the tool
does the measuring.

For agents that look things up, the documentation site has
[`llms.txt`](https://maciejziel.github.io/pytest-queryguard/llms.txt), a
summary, and
[`llms-full.txt`](https://maciejziel.github.io/pytest-queryguard/llms-full.txt),
every page in one Markdown file (see [llmstxt.org](https://llmstxt.org/)).

For an agent that speaks MCP, the [MCP server](mcp.md) gives the same loop
as tools: `run_tests` and the static check, `lint`.

## Claude Code plugin

The repository is a Claude Code plugin marketplace with one plugin, which
installs the skill below from the `main` branch; the skill works with any
1.x release of the package. In a terminal:

```console
claude plugin install pytest-queryguard --marketplace MaciejZiel/pytest-queryguard
```

or, inside Claude Code,
`/plugin install pytest-queryguard --marketplace MaciejZiel/pytest-queryguard`.
Claude Code before 2.1.275 needs two steps:

```console
claude plugin marketplace add MaciejZiel/pytest-queryguard
claude plugin install pytest-queryguard@pytest-queryguard
```

Add `--scope project` to the install to record it in the project's
`.claude/settings.json`, for everyone who works on it. Claude Code then loads
the skill when the task matches its description, or on
`/pytest-queryguard:pytest-queryguard`. `claude plugin update
pytest-queryguard@pytest-queryguard` gets the skill as it is on `main` now
(the plugin has no version number of its own, so each commit is a new
version).

The plugin does not start the [MCP server](mcp.md): that one has to run
with the interpreter of your project, so add it per project as its page
says.

## A skill (Claude Code and other agents that read SKILL.md)

The repository has a skill,
[`skills/pytest-queryguard/SKILL.md`](https://github.com/MaciejZiel/pytest-queryguard/blob/main/skills/pytest-queryguard/SKILL.md).
Without the plugin, copy it into your project, where Claude Code finds
project skills (Codex and other agents that read `SKILL.md` files have
their own skills directory):

```console
mkdir -p .claude/skills/pytest-queryguard
curl -fsSL -o .claude/skills/pytest-queryguard/SKILL.md \
  https://raw.githubusercontent.com/MaciejZiel/pytest-queryguard/main/skills/pytest-queryguard/SKILL.md
```

(`~/.claude/skills/pytest-queryguard/` makes it available in every
project.) Its description makes the agent load it when the user mentions
N+1 queries, "too many queries", a lazy load in a loop, `assertNumQueries`
or a query count test, or an alternative to nplusone or django-zeal; after
changing code that loads ORM objects; and when a test fails with
`queryguard: N+1 detected`. It tells the agent to:

1. check that the plugin is installed in the project's environment;
2. run the tests of the changed code with `--queryguard-json` and
   `--queryguard-write-patch`;
3. read the findings from the JSON file (`kind`, `label`, `file`, `line`,
   `message` with its `fix:` line) and apply the loader option on the query
   that loaded the parents, or the patch with `git apply`;
4. run the same tests again, and not to silence findings unless asked.

## AGENTS.md

Paste this into the `AGENTS.md` (or `CLAUDE.md`) of your project, and change
the paths:

```markdown
## N+1 queries (pytest-queryguard)

pytest-queryguard fails any test whose application code runs an N+1 query
(N+1s in test code itself are only counted at the end of the run).
After changing code that queries or loops over ORM objects:

1. Run the tests that cover it:
   `python -m pytest tests/test_orders.py --queryguard-json=.queryguard.json --queryguard-write-patch=.queryguard.patch -q`
2. Read `.queryguard.json`: `tests[<node id>].findings[]` has `kind`,
   `label`, `file`, `line`, `count` and `message`. The `message` names the
   query that loaded the parents (`parents loaded at`) and the fix (`fix:`).
3. Fix at that query, not in the loop: `joinedload(...)` for a many-to-one,
   `selectinload(...)` for a collection (Django: `select_related(...)`,
   `prefetch_related(...)`); for `repeated-statement`, one query
   with `IN (...)`. If `.queryguard.patch` is not empty, `git apply` it from
   pytest's rootdir.
4. Re-run the same command; done when the findings are gone and tests pass.

Do not add `ignore()`, `queryguard_ignore` markers or baseline entries to
make a test pass unless asked. Without a database,
`python -m pytest_queryguard.lint --models app.models app/` is a quick
static check; the tests are the proof.
```

## A Cursor rule

Save as `.cursor/rules/pytest-queryguard.mdc`; Cursor attaches it when the
agent edits a file the globs match:

```markdown
---
description: Find and fix N+1 queries with pytest-queryguard after changing SQLAlchemy or Django ORM data access
globs: app/**/*.py,tests/**/*.py
alwaysApply: false
---

After changing code that queries or loops over SQLAlchemy or Django objects, run the
tests that cover it with
`python -m pytest <tests> --queryguard-json=.queryguard.json --queryguard-write-patch=.queryguard.patch -q`.
Read the findings in `.queryguard.json` (`tests[...].findings[]`: `kind`,
`label`, `file`, `line`, `message`). Put the loader option on the query
named by `parents loaded at` in `message` (`joinedload` for many-to-one,
`selectinload` for collections; Django: `select_related`,
`prefetch_related`), or `git apply .queryguard.patch`. Re-run
the same command until the findings are gone. Never silence a finding with
`ignore()`, a marker or a baseline entry unless asked.
```

## Why the JSON file

The terminal report is written for people and its wording changes between
releases (see [Stability](stability.md#what-is-not-public)); the
`--queryguard-json` file has [documented keys](ci.md#query-counts-as-json)
and a `format` number. `message` inside it is the report's text, for the
`fix:` and `parents loaded at` lines, which an agent reads as hints. The
[patch](when-a-test-fails.md#a-patch-for-the-parent-query) is written only
when the line of the parent query is certain.
