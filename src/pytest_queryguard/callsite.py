from __future__ import annotations

import os
import sys
import sysconfig
from pathlib import Path

from .events import CallSite


def _package_dir(name: str) -> str | None:
    module = sys.modules.get(name)
    file = getattr(module, "__file__", None)
    return str(Path(file).resolve().parent) + os.sep if file else None


def _compute_prefixes() -> tuple[str, ...]:
    paths: list[str | None] = [
        _package_dir("sqlalchemy"),
        _package_dir("pytest"),
        _package_dir("_pytest"),
        _package_dir("pluggy"),
        str(Path(__file__).resolve().parent) + os.sep,
    ]
    for key in ("stdlib", "platstdlib"):
        path = sysconfig.get_paths().get(key)
        if path:
            paths.append(path.rstrip(os.sep) + os.sep)
            paths.append(str(Path(path).resolve()) + os.sep)
    return tuple(dict.fromkeys(os.path.normcase(p) for p in paths if p))


_prefixes: tuple[str, ...] | None = None


def _is_internal(filename: str) -> bool:
    global _prefixes
    if filename.startswith("<"):
        return True
    if _prefixes is None:
        _prefixes = _compute_prefixes()
    if os.path.normcase(filename).startswith(_prefixes):
        return True
    parts = Path(filename).parts
    return "site-packages" in parts or "dist-packages" in parts


def find_callsite() -> CallSite | None:
    """First stack frame outside SQLAlchemy, pytest, stdlib, site-packages and us."""
    frame = sys._getframe(1)
    while frame is not None:
        filename = frame.f_code.co_filename
        if not _is_internal(filename):
            return CallSite(filename, frame.f_lineno, frame.f_code.co_name)
        frame = frame.f_back  # type: ignore[assignment]
    return None
