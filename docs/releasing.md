# Releasing

A stable release should never need a fix made in a hurry afterwards, so
everything that can be checked is checked before the tag, by one workflow: the
release gate (`.github/workflows/release-gate.yml`). It does not tag, upload or
publish anything. The release workflow (`.github/workflows/release.yml`) starts
from a tag that a person pushes, and a person pushes it only after the gate is
green on the commit that gets the tag.

## What the gate checks

| # | check | where |
|---|---|---|
| 1 | The whole CI matrix (`ci.yml`, reused through `workflow_call`): Python 3.10 to 3.14, SQLAlchemy 2.0 and latest, pytest 8.0 and latest, xdist, PostgreSQL, Windows and macOS. Plus the oldest SQLAlchemy series and pytest on Linux, Windows and macOS. | `ci`, `oldest-versions` jobs |
| 2 | Accuracy corpus: precision and recall of the default configuration are at least the minimums in `scripts/accuracy_thresholds.json` (see [Accuracy](accuracy.md#release-thresholds)), and no disagreement with a verdict is undocumented. | `release_gate.py accuracy` |
| 3 | Every Python block in `README.md` and `docs/` parses, and the projects a page spells out file by file (and `README.md`'s quick start, and the examples in `examples/`) run and give the output the page shows. | `tests/test_doc_examples.py`, `test_readme.py`, `test_docs.py`, `test_examples.py` |
| 4 | The wheel and the sdist install into clean virtual environments on Linux, Windows and macOS; `pip check` passes; the package imports; the `pytest11` entry point is registered; `pytest --co` works on a tiny project and the plugin reports an N+1 there. | `release_gate.py install` |
| 5 | Upgrade: the newest earlier final release from PyPI is installed, the new wheel is installed over it, and the tiny project runs before and after. | `release_gate.py upgrade` |
| 6 | Overhead: the per-statement overhead of the measurement is at most 125 % of `scripts/bench_baseline.json` (Core and ORM loops), measured as in [How the overhead is measured](#how-the-overhead-is-measured). | `release_gate.py bench` |
| 7 | The wheel has `py.typed`, `LICENSE`, the entry point and only the package; no tests, docs, scripts, caches or scratch files. The sdist has the license, README, changelog, `pyproject.toml` and sources and no scratch files. | `release_gate.py contents` |
| 8 | `__version__`, the top entry of `CHANGELOG.md`, its date (a valid date, not in the future, not older than the entry below it) and the link references at the bottom (`[Unreleased]` compares the newest tag with `HEAD`, each entry compares the previous tag with itself) agree. | `release_gate.py consistency` |

## Running it

On GitHub: Actions, "Release gate", "Run workflow", on `main`. It also runs on
a pull request that changes `src/pytest_queryguard/__init__.py` (the version),
the workflow or its scripts. The `gate` job is green only when every other job
is, so it is the one to require.

Locally, the checks that are not a plain `pytest` run:

```bash
uv build --out-dir dist
uv run python scripts/release_gate.py all dist
```

or one at a time: `consistency`, `accuracy`, `contents dist`, `install dist`,
`upgrade dist`, `bench`. `install` and `upgrade` create virtual environments
in a temporary directory and need network access to PyPI.
`QUERYGUARD_REQUIRE_DOC_EXAMPLES=1 uv run pytest tests/test_doc_examples.py`
makes a missing package a failure instead of a skip for the documentation
projects (only Litestar, pytest-asyncio and SQLModel may be missing).

## Steps of a release

1. A pull request bumps `__version__`, dates the changelog entry and fixes the
   link references. The gate runs on it.
2. After it is merged, run the gate on `main` and wait for it to be green.
3. Tag that commit `vX.Y.Z` and push the tag; `release.yml` builds and
   publishes.

## How the overhead is measured

A shared GitHub runner is noisy, and the noise is not the same on every
machine: with the earlier method (each configuration in its own process, the
median of 15) the same code measured a Core ratio from 0.27 to 0.45 on
different runners, and the same high value again when it was measured a
second time on the same runner, so the gate failed about every other run on
unchanged code.

`scripts/bench_gate.py` therefore times the measurement off and on in the
same process: 7 processes (`--processes`), each with 20 rounds (`--rounds`)
of a block of 1000 statements off and a block on, in alternating order, with
a new collector for each "on" block and a garbage collection before every
block. A process's ratio is (on − off) / off of its fastest "off" and fastest
"on" block, since noise only adds time; the result is the median over the
processes. When a loop is over the limit, the gate measures again, up to 3
times in all (`--attempts`), and the best result of each loop counts. The
check takes about two minutes on the runner, at most about six with all
three attempts.

A real regression is in every attempt: a plugin 1.5 times slower per
statement raises both ratios to about 1.5 times the baseline, well over the
125 % limit.

## When a threshold has to change

The accuracy minimums and the overhead baseline are floors against
regressions. Raise them in the pull request that improves the result. Lower
them only in a pull request that says why. The overhead baseline is recorded on the GitHub runner
(`ubuntu-latest`, Python 3.12) that runs the gate, because the ratio depends on
the machine: it is the mean of the gate's results over several green runs
there (Core 0.297, ORM 0.267). Record it from those runs, not from a laptop: save the
log of the `overhead` job of each green gate run and let the script average
the `ratios:` lines,

```bash
gh run view RUN_ID --log --job JOB_ID > gate-1.log   # for each green run
uv run python scripts/bench_gate.py --record gate-*.log
```

It refuses a log without a passing overhead check. `--update` measures on the
current machine and writes the result, which is only right on that runner.
