from __future__ import annotations

from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

_DJANGO_PROJECT = (Path(__file__).parent / "django_project").resolve()


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    """Leave the Django project out unless it is what pytest was asked to run.

    Its conftest configures Django in the process, after which pytest-django
    takes part in every test of the session; it runs on its own:
    ``uv run --group django pytest tests/django_project``.
    """
    if collection_path.name != _DJANGO_PROJECT.name:
        return None
    if collection_path.resolve() != _DJANGO_PROJECT:
        return None
    for arg in config.args:
        target = Path(config.invocation_params.dir, arg.split("::")[0]).resolve()
        if target == _DJANGO_PROJECT or _DJANGO_PROJECT in target.parents:
            return None
    return True


# What every inner (pytester) session gets in PYTEST_ADDOPTS, see below.
INNER_ADDOPTS = "-p no:randomly"
TEST_CODE_FAILS = "-o queryguard_test_code=fail"


@pytest.fixture(autouse=True)
def _pytester_sessions_keep_their_order(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run the pytester sessions in file order even if pytest-randomly is installed.

    The files these tests write depend on their order (a table one test makes,
    a fixture that runs first), so shuffling them is not what is under test.
    The outer run keeps its own order. Pytester clears PYTEST_ADDOPTS when it is
    set up, so it is requested first. This is a fixture and not a hook wrapper
    because a wrapper adds frames to the stack the tests run on, and some of
    them probe the recursion limit.

    CI and BUILD_NUMBER are removed for the same reason: pytest prints every
    failure message in full in the short summary when one is set, so an
    inner session's output would differ between CI and a local run.

    The inner sessions also report the N+1s of test code
    (``queryguard_test_code = fail``): most of these tests write the loop
    under test in the test function itself, as the shortest way to make one.
    A test of the default (``off``) sets ``INNER_ADDOPTS`` on its own.
    """
    if "pytester" in request.fixturenames:
        request.getfixturevalue("pytester")
        monkeypatch.setenv("PYTEST_ADDOPTS", INNER_ADDOPTS + " " + TEST_CODE_FAILS)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("BUILD_NUMBER", raising=False)
