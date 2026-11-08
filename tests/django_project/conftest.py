"""Configure the test project before pytest-django sets up its database."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("django")
pytest.importorskip("pytest_django")

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.django_project.settings")

import django

django.setup()

from .library.seed import seed


@pytest.fixture
def seeded(db: None) -> None:
    seed()
