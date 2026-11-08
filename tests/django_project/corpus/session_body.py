"""Run the Django corpus scenarios as the tests of a pytest-django session.

Copied next to the corpus conftest and run once per configuration. The seed
rows and ``setup()`` are a fixture, so they are not measured; the test body
is the scenario's ``run()``.
"""

from __future__ import annotations

import importlib
import os

import pytest

from tests.django_project.library.seed import seed

NAMES = os.environ["CORPUS_SCENARIOS"].split(",")


@pytest.fixture
def prepared(request, db):
    module = importlib.import_module(
        f"tests.django_project.corpus.scenarios.{request.param}"
    )
    seed()
    setup = getattr(module, "setup", None)
    if setup is not None:
        setup()
    return module


@pytest.mark.parametrize("prepared", NAMES, indirect=True)
def test_scenario(prepared):
    prepared.run()
