"""Settings of the test project. ``QUERYGUARD_DJANGO_DB`` picks the database:
unset for SQLite, or a URL ``postgresql://user:password@host:port/name`` or
``mysql://user:password@host:port/name``. pytest-django creates its own test
database next to the one named."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import unquote, urlsplit

SECRET_KEY = "only-for-the-tests"  # not a secret: a test project
DEBUG = False
USE_TZ = True
ALLOWED_HOSTS = ["testserver"]
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "rest_framework",
    "tests.django_project.library",
]
MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]
ROOT_URLCONF = "tests.django_project.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


def _database(url: str | None) -> dict[str, Any]:
    if not url:
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
    parts = urlsplit(url)
    engine = {
        "postgresql": "django.db.backends.postgresql",
        "postgres": "django.db.backends.postgresql",
        "mysql": "django.db.backends.mysql",
    }[parts.scheme.split("+")[0]]
    return {
        "ENGINE": engine,
        "NAME": parts.path.lstrip("/"),
        "USER": unquote(parts.username or ""),
        "PASSWORD": unquote(parts.password or ""),
        "HOST": parts.hostname or "",
        "PORT": str(parts.port or ""),
    }


DATABASES = {"default": _database(os.environ.get("QUERYGUARD_DJANGO_DB"))}
if DATABASES["default"]["ENGINE"].endswith("mysql"):
    # PyMySQL (MIT, dev group) in place of mysqlclient (GPL).
    import pymysql

    pymysql.version_info = (2, 2, 7, "final", 0)
    pymysql.install_as_MySQLdb()
