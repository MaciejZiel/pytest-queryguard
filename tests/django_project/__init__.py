"""The Django integration's tests: a small Django project run with pytest-django.

Run on its own, with the ``django`` dependency group::

    uv run --group django pytest tests/django_project

The main suite leaves this directory out (see ``tests/conftest.py``): this
conftest configures Django in the process, and pytest-django then takes part
in every test of the session.
"""
