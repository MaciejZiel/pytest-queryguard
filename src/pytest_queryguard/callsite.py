from __future__ import annotations

import os
import sys
import sysconfig
from collections.abc import Iterator
from pathlib import Path
from types import FrameType

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


def _parent_greenlet_frames() -> Iterator[FrameType]:
    """Frames of the greenlets above the current one, innermost first.

    SQLAlchemy runs the sync code behind an async session in a child greenlet,
    so the awaiting user code is only reachable through the parent greenlet.
    """
    module = sys.modules.get("greenlet")
    if module is None:
        return
    try:
        parent = module.getcurrent().parent
        while parent is not None:
            frame = parent.gr_frame
            if frame is not None:
                yield frame
            parent = parent.parent
    except Exception:  # noqa: BLE001
        return


def _first_external(frame: FrameType | None) -> CallSite | None:
    while frame is not None:
        filename = frame.f_code.co_filename
        if not _is_internal(filename):
            return CallSite(filename, frame.f_lineno, frame.f_code.co_name)
        frame = frame.f_back
    return None


def find_callsite() -> CallSite | None:
    """First stack frame outside SQLAlchemy, pytest, stdlib, site-packages and us.

    When the stack ends inside a greenlet (async sessions), the search goes on
    in the parent greenlets, where the awaiting user code lives.
    """
    site = _first_external(sys._getframe(1))
    if site is not None:
        return site
    for frame in _parent_greenlet_frames():
        site = _first_external(frame)
        if site is not None:
            return site
    return None
