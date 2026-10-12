"""Run the corpus scenarios as the tests of a pytest session.

The runner copies this file next to a ``conftest.py`` and starts pytest on it,
once per configuration of the tool under measurement. The setup is a fixture,
so it is not measured; the test body is the scenario's ``run()``.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import os

import pytest

from tests.corpus import kit

NAMES = os.environ["CORPUS_SCENARIOS"].split(",")


@pytest.fixture
def prepared(request, tmp_path):
    module = importlib.import_module(f"tests.corpus.scenarios.{request.param}")
    ctx = kit.Ctx(tmp_path)
    ctx.reset()
    module.setup(ctx)
    ctx.warm_up()
    yield module, ctx
    ctx.dispose()


@pytest.mark.parametrize("prepared", NAMES, indirect=True)
def test_scenario(prepared):
    module, ctx = prepared
    if inspect.iscoroutinefunction(module.run):

        async def main() -> None:
            try:
                await module.run(ctx)
            finally:
                await ctx.dispose_async()

        asyncio.run(main())
    else:
        module.run(ctx)
