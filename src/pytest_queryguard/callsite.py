from __future__ import annotations

import itertools
import os
import sys
import sysconfig
from collections.abc import Iterable, Iterator
from pathlib import Path
from types import CodeType, FrameType
from typing import NamedTuple

from ._compat import FLUSH_CONTEXT, FLUSH_FUNCTION, SESSION_FILE
from .events import CallSite


def _package_dir(name: str) -> str | None:
    module = sys.modules.get(name)
    file = getattr(module, "__file__", None)
    return str(Path(file).resolve().parent) + os.sep if file else None


# Packages whose frames are never the application's. pluggy is also where the
# walk for the callers stops (see _callers).
_HOOK_PACKAGE = "pluggy"
_INTERNAL_PACKAGES = ("sqlalchemy", "pytest", "_pytest", _HOOK_PACKAGE)


def _compute_prefixes() -> tuple[str, ...]:
    paths: list[str | None] = [_package_dir(name) for name in _INTERNAL_PACKAGES]
    paths.append(str(Path(__file__).resolve().parent) + os.sep)
    for key in ("stdlib", "platstdlib"):
        path = sysconfig.get_paths().get(key)
        if path:
            paths.append(path.rstrip(os.sep) + os.sep)
            paths.append(str(Path(path).resolve()) + os.sep)
    return tuple(dict.fromkeys(os.path.normcase(p) for p in paths if p))


_prefixes: tuple[str, ...] | None = None
# filename -> internal?  A suite touches a few hundred files at most, and the
# answer never changes while the process runs, so each is classified once.
_internal: dict[str, bool] = {}


def _classify(filename: str) -> bool:
    global _prefixes
    if filename.startswith("<"):
        return True
    if _prefixes is None:
        _prefixes = _compute_prefixes()
    if os.path.normcase(filename).startswith(_prefixes):
        return True
    parts = Path(filename).parts
    return "site-packages" in parts or "dist-packages" in parts


def _is_internal(filename: str) -> bool:
    try:
        return _internal[filename]
    except KeyError:
        result = _internal[filename] = _classify(filename)
        return result


# code object -> is it a method of a Session subclass? A Session subclass in the
# application's code (SQLModel's, installed in editable mode, or an app's own
# override of execute()) is part of the session: every query of the test would
# otherwise share its `super().execute()` line as call site, and lookups made
# at different lines would look like one query line returning many rows.
# Decided once per code object, from the `self` of its first run: a mixin
# method shared by a Session subclass and another class keeps that answer.
# Keyed by id(): a code object's hash goes over its bytecode and constants,
# nested functions included, which is slow for a module's code. The code
# object is kept with the answer, so its id is not reused while it is there.
_session_methods: dict[int, tuple[CodeType, bool]] = {}
_MAX_SESSION_METHODS = 4096


def _session_classes() -> tuple[type, ...]:
    classes: list[type] = []
    # only modules already loaded: an instance of one has imported it
    for module_name, name in (
        ("sqlalchemy.orm.session", "Session"),
        ("sqlalchemy.ext.asyncio.session", "AsyncSession"),
    ):
        cls = getattr(sys.modules.get(module_name), name, None)
        if isinstance(cls, type):
            classes.append(cls)
    return tuple(classes)


def _is_session_method(frame: FrameType) -> bool:
    """Whether ``frame`` runs a method of a ``Session`` or ``AsyncSession`` subclass."""
    if frame.f_code.co_name in _INLINED:
        frame = _outer(frame)  # a comprehension in the method, before 3.12
    code = frame.f_code
    known = _session_methods.get(id(code))
    if known is not None and known[0] is code:
        return known[1]
    result = False
    if code.co_argcount and code.co_varnames[0] == "self":
        try:
            this = frame.f_locals.get("self")
            result = isinstance(this, _session_classes())
        except Exception:  # noqa: BLE001  # a plugin bug must never break the user suite
            result = False
    if len(_session_methods) >= _MAX_SESSION_METHODS:
        _session_methods.clear()
    _session_methods[id(code)] = (code, result)
    return result


def _parent_greenlet_frames() -> Iterator[FrameType]:
    """Frames of the greenlets above the current one, innermost first.

    SQLAlchemy runs the sync code behind an async session in a child greenlet,
    so the awaiting user code is only reachable through the parent greenlet.
    """
    # greenlet comes with SQLAlchemy's async support; only used if already loaded.
    # Code running inside a SQLAlchemy greenlet has imported it already, while
    # an import here, after pytester purges sys.modules between runs, could
    # create a second greenlet module object next to the one SQLAlchemy uses.
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
    except Exception:  # noqa: BLE001  # a plugin bug must never break the user suite
        return


# (filename, line, function) -> CallSite.  A suite runs the same few hundred
# lines over and over, and CallSite is immutable, so one object per location
# is shared instead of building a new one for every statement. Bounded, so
# generated code cannot grow it without limit. Emptied when a measurement
# starts (new_measurement()), so that the first statement of each line in it
# builds a new CallSite with that run's callers. The in-app mode passes a cache
# of each unit's own instead (find_callsite(sites)): its units overlap.
Sites = dict[tuple[str, int, str], CallSite]
_sites: dict[tuple[str, int, str], CallSite] = {}
_MAX_SITES = 4096
DEFAULT_STACK_DEPTH = 3
_stack_depth = DEFAULT_STACK_DEPTH


def new_measurement() -> None:
    """Build each call site again, with its callers, the next time its line runs.

    The CallSite objects already handed out keep their callers: an outer
    measurement's events are not changed by an inner one starting.
    """
    _sites.clear()
    _paths.clear()


def swap_stack_depth(depth: int) -> int:
    """Set how many callers are recorded per call site; return the previous value."""
    global _stack_depth
    old, _stack_depth = _stack_depth, depth
    return old


_hook_prefix: str | None = None


def _hook_caller() -> str:
    """The directory of pluggy, which calls the test function and the fixtures."""
    global _hook_prefix
    if _hook_prefix is None:
        _hook_prefix = os.path.normcase(_package_dir(_HOOK_PACKAGE) or "")
    return _hook_prefix


# Before 3.12 a list, dict or set comprehension runs in a frame of its own,
# called by the function it is written in, at the same place (PEP 709 inlined
# them). Reports name that function, as they do on 3.12 and later.
_INLINED = (
    frozenset({"<listcomp>", "<dictcomp>", "<setcomp>"})
    if sys.version_info < (3, 12)
    else frozenset()
)


def _outer(frame: FrameType) -> FrameType:
    """The frame of the function a comprehension is written in, or ``frame``."""
    while frame.f_code.co_name in _INLINED:
        back = frame.f_back
        if back is None or back.f_code.co_filename != frame.f_code.co_filename:
            break
        frame = back
    return frame


def _app_frames(frame: FrameType | None) -> Iterator[FrameType]:
    """The application frames from ``frame`` outwards.

    A comprehension's frame (before 3.12) is given as the function it is
    written in. The walk ends at the first pluggy frame: the hook call that
    ran the test or the fixture. Above it is pytest itself and its console
    script.
    """
    stop = _hook_caller()
    while frame is not None:
        code = frame.f_code
        if stop and os.path.normcase(code.co_filename).startswith(stop):
            return
        if not _is_internal(code.co_filename) and not _is_session_method(frame):
            if code.co_name in _INLINED:
                frame = _outer(frame)
            yield frame
        frame = frame.f_back


def _callers(frame: FrameType | None, depth: int) -> tuple[CallSite, ...]:
    """Up to ``depth`` application frames from ``frame`` outwards."""
    return tuple(
        CallSite(
            filename=app.f_code.co_filename,
            lineno=app.f_lineno,
            function=app.f_code.co_name,
        )
        for app in itertools.islice(_app_frames(frame), depth)
    )


Entry = tuple[tuple[str, int], ...]
_MAX_ENTRY_LINES = 64


def find_entry() -> Entry | None:
    """The application lines the running statement came from, up to the test.

    ``(filename, line)`` of each application frame, innermost first, as
    ``_app_frames`` walks them, through the parent greenlets of an async session.
    A function that is on the stack more than once (recursion) is kept at its
    innermost frame only, so each depth of a recursive walk has the same
    lines. Two units of work begun from the same lines are passes of one loop
    (or tasks of one fan-out); begun from different lines, they may be calls
    on separate lines. None when no application frame is found.
    """
    return _site_origin(sys._getframe(1), None, False)[1].path


class Origin(NamedTuple):
    """Where a statement or a load came from (``find_origin``).

    ``path`` is the lines of ``find_entry``: two statements with the same
    path are passes of one loop, two with different paths may be calls on
    separate lines. ``entry`` is the end of the path that is test code: the
    lines of the test (and its helpers) that called the application; None
    when the test code called no application code (``test_code``) or none of
    the path is test code (a thread of the application). ``test_code`` is True when every
    application frame is test code (see TestCode): a statement or load of
    the test itself, not of the application.
    """

    path: Entry | None = None
    entry: Entry | None = None
    test_code: bool = False


NO_ORIGIN = Origin()


def find_origin() -> Origin:
    """The origin of the running statement or load; see Origin."""
    return _site_origin(sys._getframe(1), None, False)[1]


def find_site_origin(sites: Sites | None = None) -> tuple[CallSite | None, Origin]:
    """``find_callsite(sites)`` and ``find_origin()`` in one walk of the stack."""
    return _site_origin(sys._getframe(1), _sites if sites is None else sites, True)


def _site_origin(
    first: FrameType, sites: Sites | None, want_site: bool
) -> tuple[CallSite | None, Origin]:
    """(call site, origin): see find_callsite and find_origin.

    One walk from ``first`` outwards, through the parent greenlets when the
    walk did not reach the test: the first application frame is the call
    site, as ``_first_external`` finds it; the application frames up to the
    hook call that ran the test, as ``_app_frames`` walks them, are the
    origin. The tuples of the origin are interned per measurement, so the
    events of one loop share them; it is ``NO_ORIGIN`` when no application
    frame is found.
    """
    lines: list[tuple[str, int]] = []
    entry: list[tuple[str, int]] = []  # the test lines since the last other
    seen: set[int] = set()  # id() of the code objects: they are on the stack
    test = True
    site: CallSite | None = None
    skipped: FrameType | None = None  # the first method of a Session subclass
    walking = True  # the origin: until the hook call that ran the test
    library = False  # a _LIBRARY frame since the last application frame
    kinds = _kinds
    frame: FrameType | None = first
    parents: Iterator[FrameType] | None = None
    while True:
        if frame is None:
            # the end of this greenlet's stack: the awaiting code of an async
            # session is in the parent greenlets
            if parents is None:
                parents = _parent_greenlet_frames()
            frame = next(parents, None)
            if frame is None:
                break
        code = frame.f_code
        filename = code.co_filename
        kind = kinds.get(filename)
        if kind is None:
            kind = _kind(filename)
        if kind:
            if kind == _HOOK and walking:
                walking = False
                if site is not None or not want_site:
                    break
            elif kind == _LIBRARY:
                library = True
            frame = frame.f_back
            continue
        if _is_session_method(frame):
            if skipped is None:
                skipped = frame
            frame = frame.f_back
            continue
        if site is None and sites is not None:
            site = _site(frame, sites)
        if code.co_name in _INLINED:
            frame = _outer(frame)
            code = frame.f_code
        if walking:
            ours = _in_test_code(filename, frame)
            if not ours or library:
                # a library between this test frame and the statement may be
                # the application, installed in site-packages: not the test's
                test = False
                entry.clear()
                library = False
            if id(code) not in seen:
                seen.add(id(code))
                line = (filename, frame.f_lineno)
                if len(lines) < _MAX_ENTRY_LINES:
                    lines.append(line)
                if ours and len(entry) < _MAX_ENTRY_LINES:
                    entry.append(line)
        elif site is not None or not want_site:
            break
        frame = frame.f_back
    if site is None and skipped is not None and sites is not None:
        site = _site(skipped, sites)
    if not lines:
        return site, NO_ORIGIN
    if test or not entry:
        return site, Origin(_intern(tuple(lines)), None, test)
    return site, Origin(_intern(tuple(lines)), _intern(tuple(entry)), False)


# filename -> _APP, _INTERNAL, _HOOK for pluggy's, where the walk for the
# origin stops (as _app_frames does), or _LIBRARY for an installed package that
# may be the application itself (installed non-editable): one lookup per frame
# of the walk. A statement that test code reaches through a _LIBRARY frame is
# not the test code's own. The packages of _TEST_LIBRARIES (factories, fake
# data) are test tools, plain _INTERNAL.
_kinds: dict[str, int] = {}
_MAX_KINDS = 4096
_APP, _INTERNAL, _HOOK, _LIBRARY = 0, 1, 2, 3
_TEST_LIBRARIES = frozenset(
    {"factory", "polyfactory", "faker", "greenlet", "anyio", "sniffio", "xdist"}
)
_INSTALLED = ("site-packages", "dist-packages")


def _kind(filename: str) -> int:
    kind = _APP
    if _is_internal(filename):
        stop = _hook_caller()
        if stop and os.path.normcase(filename).startswith(stop):
            kind = _HOOK
        else:
            kind = _INTERNAL if _known_library(filename) else _LIBRARY
    if len(_kinds) >= _MAX_KINDS:
        _kinds.clear()
    _kinds[filename] = kind
    return kind


def _known_library(filename: str) -> bool:
    """Whether an internal file is SQLAlchemy, pytest, the standard library,
    the plugin or a test tool, rather than any installed package."""
    if filename.startswith("<"):
        return True
    global _prefixes
    if _prefixes is None:
        _prefixes = _compute_prefixes()
    if os.path.normcase(filename).startswith(_prefixes):
        return True
    parts = Path(filename).parts
    for i, part in enumerate(parts[:-1]):
        if part in _INSTALLED:
            top = parts[i + 1].removesuffix(".py")
            return top in _TEST_LIBRARIES or top.startswith(("pytest", "_pytest"))
    return True


def _intern(lines: Entry) -> Entry:
    path = _paths.get(lines)
    if path is None:
        if len(_paths) >= _MAX_PATHS:
            _paths.clear()
        path = _paths[lines] = lines
    return path


# The call paths of the current measurement (find_origin), so the statements
# of one loop share one tuple. Emptied with the call sites (new_measurement).
_paths: dict[Entry, Entry] = {}
_MAX_PATHS = 4096


class TestCode:
    """Which files are the test suite's own code, for one pytest session.

    ``root`` is pytest's rootdir. A file is test code when it is a
    ``conftest.py``, a test module the session collected (``files``), a file
    below a directory named ``tests`` or ``test`` inside the rootdir, or a
    module that defines a factory_boy or polyfactory factory class.
    """

    __test__ = False  # not a test class, for pytest's collection

    def __init__(self, root: str | None = None) -> None:
        self.root = (
            os.path.normcase(os.path.realpath(root)).rstrip(os.sep) + os.sep
            if root
            else None
        )
        self.files: set[str] = set()
        self.known: dict[str, bool] = {}  # filename -> test code?

    def add_files(self, paths: Iterable[str]) -> None:
        """Add the collected test modules (absolute paths)."""
        self.files.update(os.path.normcase(os.path.realpath(p)) for p in paths)
        self.known.clear()

    def classify(self, filename: str, frame: FrameType) -> bool:
        if os.path.basename(filename) == "conftest.py":
            return True
        path = os.path.normcase(os.path.realpath(filename))
        if path in self.files:
            return True
        root = self.root
        if root is not None and path.startswith(root):
            folders = path[len(root) :].split(os.sep)[:-1]
            if any(folder in _TEST_FOLDERS for folder in folders):
                return True
        return _defines_factory(frame.f_globals)


_TEST_FOLDERS = frozenset({"tests", "test"})
# (module, class) of the base factory classes of factory_boy and polyfactory
_FACTORY_BASES = (
    ("factory.base", "BaseFactory"),
    ("polyfactory.factories.base", "BaseFactory"),
)


def _defines_factory(namespace: dict[str, object]) -> bool:
    """Whether the module of these globals defines a factory class itself."""
    bases = tuple(
        cls
        for cls in (
            getattr(sys.modules.get(module), name, None)
            for module, name in _FACTORY_BASES
        )
        if isinstance(cls, type)
    )
    if not bases:
        return False
    module = namespace.get("__name__")
    try:
        for value in list(namespace.values()):
            if (
                isinstance(value, type)
                and value.__module__ == module
                and issubclass(value, bases)
            ):
                return True
    except Exception:  # noqa: BLE001  # odd module namespaces: not a factory module
        return False
    return False


_test_code = TestCode()


def swap_test_code(new: TestCode) -> TestCode:
    """Set the test code of the running session; return the previous one."""
    global _test_code
    old, _test_code = _test_code, new
    return old


def _in_test_code(filename: str, frame: FrameType) -> bool:
    test_code = _test_code
    known = test_code.known
    try:
        return known[filename]
    except KeyError:
        if len(known) >= _MAX_KINDS:
            known.clear()
        result = known[filename] = test_code.classify(filename, frame)
        return result


def _site(frame: FrameType, sites: Sites) -> CallSite:
    code = frame.f_code
    key = (code.co_filename, frame.f_lineno, code.co_name)
    site = sites.get(key)
    if site is None:
        if len(sites) >= _MAX_SITES:
            sites.clear()
        outer = _outer(frame) if key[2] in _INLINED else frame
        site = sites[key] = CallSite(
            filename=key[0],
            lineno=key[1],
            function=outer.f_code.co_name,
            callers=_callers(outer.f_back, _stack_depth),
        )
    return site


def _first_external(
    frame: FrameType | None, sites: Sites, skipped: list[FrameType] | None = None
) -> CallSite | None:
    """The first application frame from ``frame`` outwards, as a call site.

    A method of a Session subclass is passed over (see _is_session_method);
    the first one is appended to ``skipped``, the call site to use when no
    other application frame is found.
    """
    internal = _internal
    while frame is not None:
        filename = frame.f_code.co_filename
        try:
            skip = internal[filename]
        except KeyError:
            skip = internal[filename] = _classify(filename)
        if not skip:
            if not _is_session_method(frame):
                return _site(frame, sites)
            if skipped is not None and not skipped:
                skipped.append(frame)
        frame = frame.f_back
    return None


# Session._flush holds the UOWTransaction of one flush in its local
# `flush_context`. It sits behind a generated "warned" wrapper, so the frame is
# found by function name and file. If a SQLAlchemy release renames either, no
# statement is tagged and the write-loop rule counts every statement, as before.
_SESSION_FILE = SESSION_FILE
_flush_numbers = itertools.count(1)
_MAX_FLUSH_DEPTH = 60


def find_flush() -> int | None:
    """The number of the ORM flush this statement runs in, or None.

    The statements of one flush share a number: a unit of work cannot make them
    fewer, so they are not a loop of the application's own writes.
    """
    if _SESSION_FILE is None:
        return None
    frame: FrameType | None = sys._getframe(1)
    for _ in range(_MAX_FLUSH_DEPTH):
        if frame is None:
            return None
        code = frame.f_code
        if code.co_name == FLUSH_FUNCTION and code.co_filename == _SESSION_FILE:
            context = frame.f_locals.get(FLUSH_CONTEXT)
            if context is None:
                return None
            number = getattr(context, "_queryguard_flush", None)
            if number is None:
                number = next(_flush_numbers)
                try:
                    context._queryguard_flush = number
                except AttributeError:
                    return None
            return int(number)
        frame = frame.f_back
    return None


def find_callsite(sites: Sites | None = None) -> CallSite | None:
    """First stack frame outside SQLAlchemy, pytest, stdlib, site-packages and us.

    Methods of a Session subclass are passed over too, unless no other frame
    is found: they are part of the session, not the line that ran the query.

    When the stack ends inside a greenlet (async sessions), the search goes on
    in the parent greenlets, where the awaiting user code lives. ``sites`` is
    the cache of call sites to use instead of the process-wide one.
    """
    if sites is None:
        sites = _sites
    skipped: list[FrameType] = []
    site = _first_external(sys._getframe(1), sites, skipped)
    if site is not None:
        return site
    for frame in _parent_greenlet_frames():
        site = _first_external(frame, sites, skipped)
        if site is not None:
            return site
    return _site(skipped[0], sites) if skipped else None


def trim_to_user_code(exc: BaseException) -> None:
    """End the traceback of ``exc`` at the last frame of application code.

    For the exception that queryguard_strict raises inside SQLAlchemy's lazy
    loader: the frames below the attribute access (SQLAlchemy, the plugin)
    say nothing about the N+1, so the report ends at the line that read the
    relationship. Nothing changes when no such frame is found.
    """
    last_user = None
    tb = exc.__traceback__
    while tb is not None:
        if not _is_internal(tb.tb_frame.f_code.co_filename):
            last_user = tb
        tb = tb.tb_next
    if last_user is not None and last_user.tb_next is not None:
        last_user.tb_next = None
