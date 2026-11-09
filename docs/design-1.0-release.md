# Design: from 0.10.0 to 1.0.0

What is left between 0.10.0 (2026-11-08) and 1.0.0, and how the GitHub
Action moves to a repository of its own for the GitHub Marketplace. The plan:
1.0.0rc1 at the end of November 2026, about two weeks of soak, and 1.0.0 in
December 2026 with the classifier `Development Status :: 5 - Production/Stable`,
the action in the Marketplace, fresh benchmark numbers and an article.

It was written on 2026-11-08, right after the release pull request of
0.10.0 (#369) was merged and before `v0.10.0` was tagged, and it follows
[The API freeze before 1.0](design-1.0-freeze.md), whose recommendations it
checks first.

Sizes: **S** is under an hour, **M** about half a day, **L** a day or more.

## Where the freeze audit stands

| # | Item | State |
|---|---|---|
| 1 | Baseline reader and unknown keys | Done in 0.10.0 (changelog, "Fixed"). |
| 2 | `write-loop` and the small-repeat rule stay off | Open: a decision, to be written into the 1.0.0rc1 changelog entry. |
| 3 | Public fields of `app.Finding` | Done in 0.10.0: [Stability](stability.md#the-in-application-mode) lists them and `tests/test_public_api.py` snapshots them. |
| 4 | `watch(engine, /)` positional-only | Done in 0.10.0 (`app/_listeners.py`). |
| 5, 9, 10, 12 | Documentation and docstrings | Done in 0.10.0. |
| 6 | Deprecated names | Nothing to do before 1.0: removal in 2.0, not before 2027-02-06. |
| 23 | Trend `--top` against compare `--rows` | Open, optional: the trend stays experimental in 1.0, so it can be renamed in any minor release without a deprecation. |
| 24 | Key names of the experimental MCP, trend and static-check results | Open, optional: only before promoting one of them. |
| 26 | The action's default install and the `[sqlalchemy]` extra | Open, for the release that moves SQLAlchemy into the extra; the action repository below changes how the default install works anyway. |
| 7, 8, 11, 13 to 22, 25, 27 to 29 | Keep as they are | Nothing to do. |

No rename or deprecation has to land before the freeze: the audit found none,
and nothing public since 0.10.0 needs one. The Django support, the static
check's integrations (`--format github`, the pre-commit hook, the action's
`lint` inputs, the MCP `lint` tool) and `page_report` are listed under
[Experimental](stability.md#experimental), so their shape can still change
after 1.0. `queryguard_test_code` is not experimental: its name, values and
default `off` freeze in 1.0, while which files count as test code is a
detection detail (see [What is not public](stability.md#what-is-not-public)).

## Before 1.0.0rc1 (end of November)

1. **Release 0.10.0.** #369 is merged: run the release gate on `main`, tag
   `v0.10.0`. Then update the pytest-queryguard row of the
   [comparison](comparison.md#who-is-who), which still names 0.9.0
   (2026-11-07) and targets "SQLAlchemy 2" only, once 0.10.0 is on PyPI: the
   version, its date, and Django (experimental) among the targets. S.
2. **Decide freeze item 2.** Keep `queryguard_write_loop_threshold` and
   `queryguard_small_repeat_threshold` at `0` (off), or switch one on now under
   the pre-1.0 clause. After rc1 the default is frozen until 2.0. Write the
   decision into the introduction of the 1.0.0rc1 changelog entry. S.
3. **Decide whether `queryguard_test_code` freezes as it is.** It shipped in
   0.10.0, so rc1 gives it about three weeks of use before its default `off`
   is frozen. The alternative is to list it as experimental in 1.0 and promote
   it in 1.1. Recommendation: freeze it; the option, its values and its default
   are small, and the part that will keep changing (which files are test
   code) is already outside the promise. S.
4. **#368 (a cheaper call-path walk) in or out.** It changes no report. If it
   goes into 1.0, merge it before rc1, so the soak covers it, and record the
   overhead baseline again if the gate's numbers move (see
   [Releasing](releasing.md#when-a-threshold-has-to-change)). S to M.
5. **Triage the open issues.** #249 (editor integration) has the 1.0.0
   milestone, but what shipped in 0.10.0 is [In your editor](editor.md)
   (tasks, problem matchers, the SARIF Viewer), not an extension: close it with
   a pointer to the page, or move the extension to a 1.x milestone. #246 (the
   Django backend) has the 1.1.0 milestone and shipped as experimental in
   0.10.0: keep it open for leaving experimental and say so in a comment. The
   1.0.0 milestone should then be empty. S.
6. **Dependabot.** On 2026-11-08 three alerts were open, all for
   Django REST framework in `uv.lock` (the `django` test group, not a
   dependency of the package), fixed in 3.18.2. Update the lock file in its
   own pull request so the rc1 tag has no open alert. S.
7. **Optional, while the tools are experimental:** freeze items 23 (`--top` to
   `--rows`) and 24 (`_count` keys, `kind` and `label` in the static-check
   JSON). Neither blocks 1.0; each is cheaper before more people script
   against it. M each.
8. **The rc1 release pull request.** `__version__ = "1.0.0rc1"`, a changelog
   entry `## [1.0.0rc1] - <date>` whose introduction says that the public API
   is frozen and records item 2, and the link references. `docs/index.md`,
   `docs/getting-started.md` and `llms.txt` may keep describing 0.10.0, and
   `docs/ci.md` keeps pinning the action to `v0.10.0`: for a pre-release the
   gate accepts, and for the pin requires, the newest final release, which is
   what pip installs. The classifier stays `4 - Beta`: the gate rejects
   `Production/Stable` on a pre-release. S.
9. **Gate and tag.** Run the release gate on `main` at the merged commit, tag
   `v1.0.0rc1`. The gate's upgrade check installs 0.10.0 first, then rc1 over
   it. S.
10. **Ask for the soak.** rc1 is installed only with
   `pip install --pre pytest-queryguard` or `pytest-queryguard==1.0.0rc1`. Run
   the real-world workflow (`realworld.yml`) and the Django projects of
   [Django](django.md#real-projects) on the tag. Write down what counts as a
   blocker: a regression against 0.10.0, a crash, a wrong report on a pattern
   the corpus covers, or a public name that has to change. A blocker means
   rc2 and another week of soak. S.

## Before 1.0.0 (December)

1. **Classifier.** `Development Status :: 5 - Production/Stable` in
   `pyproject.toml`. The gate's contents check fails a final 1.0.0 without
   it, so it cannot be forgotten, only found late. S.
2. **The release pull request.** `__version__ = "1.0.0"`, the dated
   changelog entry with the link references, `docs/index.md`,
   `docs/getting-started.md` and `llms.txt` on 1.0.0, and the action pin in
   `docs/ci.md` on `v1.0.0` (in the action repository's spelling if it exists
   by then, see below). S.
3. **Stability page.** Its pre-1.0 sentences are conditional ("until 1.0",
   "while the version is 0.x") and stay true, but the summary at the top
   ("Until 1.0 a minor release may still break things") should go, and the
   pre-1.0 clause can become a historical note. Say which releases get fixes
   (see the next item). S.
4. **SECURITY.md.** "The project is pre-1.0" goes. Say which versions get
   security fixes from 1.0 on (for example the latest 1.x minor release), that
   0.x releases get none, and name the action repository in the scope next to
   `action/`. S.
5. **The Marketplace action repository**, as designed below. Creating a new
   public repository needs the owner's go-ahead. M to L.
6. **Fresh benchmark numbers.** Run the head-to-head workflow
   (`head-to-head.yml`) on 1.0.0rc1 or later and update
   [Comparison](comparison.md#head-to-head-in-github-actions) and
   [Accuracy](accuracy.md); update the call-path table of
   [Performance](performance.md) (the section on the call path), whose
   columns name `main` and the release that added it; re-check the competitors' versions in the
   [comparison](comparison.md#who-is-who) and their dates. Its sentence that
   pytest-queryguard, queryspy and pytest-querycount "shipped their first
   release in the last three months" stops being true in January 2027 for
   pytest-queryguard (first release 2026-10-10). M.
7. **README.** Say that 1.0 is stable and what that promises (link
   [Stability](stability.md)), and point the action example to the action
   repository. S.
8. **The article.** Outside the repository; link it from the README or the
   docs index only if it stays at a stable URL. M.
9. **After the tag.** Close the 1.0.0 milestone and create 1.1.0 for what is
   left (Django out of experimental, the editor extension). S.

## The GitHub Marketplace action

### Why a repository of its own

The Marketplace lists an action only when its repository is public, has
exactly one `action.yml` (or `action.yaml`) at its root, and should hold only
what the action needs. Today the action lives in `action/` of this
repository, which has the package, the tests, the docs and seven workflows at
its root, so it cannot be listed. The `name` in `action.yml` must also be
unique in the Marketplace and must not be a GitHub account's name; there was
no account named `pytest-queryguard` on 2026-11-08, but the Marketplace
checks the name only when the first release is published.

### Name

`MaciejZiel/pytest-queryguard-action`, which did not exist on 2026-11-08.
The listing's name stays `pytest-queryguard`, the `name` in `action.yml`
today; if the Marketplace refuses it, `pytest-queryguard N+1 check`. The
action also needs a `branding` (icon and color) for the listing; adding it is
not a change to an input or output.

### This repository stays the source

`action/action.yml` here stays the one file that is edited, reviewed and
tested (`ci.yml`'s `action` job, `tests/test_action_args.py`,
`tests/test_action_steps.py`, the input and output snapshot in
`tests/test_public_api.py`). The action repository holds generated copies and
has no workflows and no pull requests of its own; its README says so and
points here for issues.

What changes in the action itself is the default install. Left empty,
`package` installs the plugin from the action's own checkout, one directory
above `action/`. In the action repository there is no package above the
action, so the install step chooses by what it finds:

- `$GITHUB_ACTION_PATH/../pyproject.toml` exists: this repository's layout,
  install from the checkout, as today.
- Otherwise install the wheel that the release copies into the action
  repository (`dist/pytest_queryguard-X.Y.Z-py3-none-any.whl`, the same file
  that was published to PyPI); pip still resolves pytest and SQLAlchemy from
  the index, as it does today.

So the plugin still matches the pinned ref in both places, and the
`package` input keeps its meaning. Installing
`pytest-queryguard==X.Y.Z` from PyPI instead would be simpler, but a commit
SHA of the action would then no longer fix the plugin code that runs: the
wheel would come from the index, by version, without a hash. With the wheel
in the repository, a SHA pin covers the plugin as it does today. The wheel is
pure Python and a few hundred kilobytes. This is one small change to the
install step, tested in `tests/test_action_steps.py` with and without the
`pyproject.toml` above the action.

### How a release reaches the action repository

A new job in `release.yml`, `action-repo`, runs after `publish` (so the
version it names is on PyPI before any workflow can ask for it):

1. Check out the tag of this repository and the action repository.
2. Replace the action repository's files with `action/action.yml`, an
   `action/README.md` written for the listing, `LICENSE`, and the wheel of
   the tag in `dist/`, taken from the `dist` artifact of the same run (the
   file `publish` uploaded).
3. Commit as "pytest-queryguard X.Y.Z" and tag `vX.Y.Z`. For a final release,
   also move the major tag (`v1`) to that commit; a pre-release (`1.1.0rc1`)
   gets its own tag only, so `@v1` never runs a release candidate.
4. Push, and create a GitHub release for the tag (a final release is marked
   latest; a pre-release is marked so).

The job pushes with a deploy key of the action repository (write access to
that one repository only), stored as a secret of an `action-repo` environment
in this repository, like the `pypi` environment of the publish job. A token
of this repository cannot push there, and a personal access token would
reach every repository of the account.

The first Marketplace release is published by hand: on the release page of
the action repository, "Publish this Action to the GitHub Marketplace", with
a primary category (Testing) and accepting the Marketplace Developer
Agreement, which needs two-factor authentication. Whether later releases that
the job creates through the API appear in the listing without that box has to
be checked on the first one; if they do not, step 4 creates a draft and a
person publishes it with the box ticked.

The release gate keeps its job: the action repository gets a tag only from a
`release.yml` run, which publishes only after a green gate on `main`.

### What users write

| Before | After |
|---|---|
| `uses: MaciejZiel/pytest-queryguard/action@v0.10.0` | `uses: MaciejZiel/pytest-queryguard-action@v1.0.0` (or a commit SHA of the action repository) |
| no major tag | `uses: MaciejZiel/pytest-queryguard-action@v1`, the newest 1.x final release |

The inputs, outputs, artifact name (`queryguard-json-<category>`), code
scanning category and pull-request comment marker are the same, so a
workflow that switches keeps its comparison with the base branch, its trend
and its open code scanning alerts: the new action reads the artifacts the old
one uploaded.

### Docs and promises that change

- [CI](ci.md#the-action) shows the action repository in its workflow, says
  that `@v1` follows 1.x releases and that a SHA or exact tag is the safer
  pin, and keeps one sentence about `MaciejZiel/pytest-queryguard/action@...`
  for existing workflows.
- [Stability](stability.md#files-tools-and-the-action): "The action is
  versioned with the package" stays, and holds in both places: the action
  repository's `vX.Y.Z` is the same action as this repository's `vX.Y.Z`. Add
  that the major tag moves only to final releases of that major version, and
  that `action/` here keeps working for every 1.x release.
- [Releasing](releasing.md): the new job in the steps of a release, and what
  to do when it fails after the package is on PyPI (run the job again; it
  only copies files, so it can be re-run).
- `scripts/release_gate.py` (`ACTION_PIN`) and `tests/test_release_gate.py`
  accept the pin in `docs/ci.md` in the action repository's spelling.
- SECURITY.md names the action repository in the scope.

### Migration for `MaciejZiel/pytest-queryguard/action@vX` users

Nothing breaks. Existing tags never move, so every pin keeps running what it
ran. Every 1.x release still tags this repository, and `action/` stays in it,
so `MaciejZiel/pytest-queryguard/action@v1.2.0` works too, and Dependabot
keeps proposing those updates. Switching is a one-line change of the `uses:`
line, listed in the 1.0.0 changelog under "Changed" and in [CI](ci.md).

A later 1.x release may make the action print a `::notice::` when it runs
from `action/` of this repository, naming the new spelling; that adds
nothing to the inputs or outputs. Removing `action/` from this repository
would break pins of the subdirectory and is a 2.0 decision at the earliest.

### What needs the owner

- Creating the public repository `MaciejZiel/pytest-queryguard-action`.
- The deploy key, its secret and the `action-repo` environment.
- Accepting the Marketplace Developer Agreement and publishing the first
  release.

Until those exist, 1.0.0 can ship with the action where it is; the
repository can follow in a 1.x minor release, since it adds a way to use
the action and changes nothing of the action's inputs or outputs.
