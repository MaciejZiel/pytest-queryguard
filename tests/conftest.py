from __future__ import annotations

import os
import time

import pytest

pytest_plugins = ["pytester"]

_AUTOVACUUM_SWITCHED_OFF = pytest.StashKey[bool]()


def _pg_autovacuum(url: str, enabled: bool) -> bool:
    """Turn autovacuum on or off for the whole test server; False if it cannot be."""
    from sqlalchemy import create_engine, text

    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            if enabled:
                conn.execute(text("ALTER SYSTEM RESET autovacuum"))
            else:
                conn.execute(text("ALTER SYSTEM SET autovacuum = off"))
            conn.execute(text("SELECT pg_reload_conf()"))
            wanted = "on" if enabled else "off"
            for _ in range(50):  # the reload is asynchronous
                with engine.connect() as fresh:
                    if fresh.execute(text("SHOW autovacuum")).scalar() == wanted:
                        return True
                time.sleep(0.05)
    except Exception:  # noqa: BLE001  # pragma: no cover - not a superuser, or no server
        return False
    finally:
        engine.dispose()
    return False  # pragma: no cover


def pytest_configure(config: pytest.Config) -> None:
    """Keep autovacuum off while the PostgreSQL tests run.

    The tests create, fill and drop tables all the time. An automatic ANALYZE
    that lands in the middle of that changes the statistics a plan is read
    from, and it holds a lock that a DROP SCHEMA ... CASCADE or DROP TABLE
    deadlocks with (SQLSTATE 40P01), so a test or its fixture fails now and
    then. Only the main process does this, so xdist workers do not race on it.
    """
    url = os.environ.get("QUERYGUARD_PG_URL")
    if not url or hasattr(config, "workerinput"):
        return
    config.stash[_AUTOVACUUM_SWITCHED_OFF] = _pg_autovacuum(url, enabled=False)


def pytest_unconfigure(config: pytest.Config) -> None:
    url = os.environ.get("QUERYGUARD_PG_URL")
    if url and config.stash.get(_AUTOVACUUM_SWITCHED_OFF, False):
        _pg_autovacuum(url, enabled=True)
