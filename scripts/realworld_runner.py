"""Test runners for the ``manage.py test`` projects of .github/workflows/realworld.yml.

A Django project of scripts/realworld_django_projects.json with a
``test_command`` runs its suite with Django's own runner, not pytest. The
workflow copies this file to a directory on PYTHONPATH and runs the command
twice: once with ``--testrunner realworld_runner.Baseline`` (Django's
``DiscoverRunner``) and once with ``--testrunner realworld_runner.Guarded
--queryguard-mode warn`` (``pytest_queryguard.django.QueryGuardRunner``).

Each run leaves in ``$REALWORLD_OUT`` what scripts/realworld_summary.py
reads after a pytest run: ``<REALWORLD_RUN>.xml``, the outcome of every test
as JUnit XML. The run with the plugin also writes ``queryguard.json`` (the
findings of each test that the runner reported, in the keys of
``--queryguard-json`` that the summary reads) and ``probe.jsonl`` (every
``QueryGuardInternalWarning``, as scripts/realworld_probe.py records them
under pytest). The findings are taken from the runner's own
``make_findings`` call, so the harness does not parse the printed report.

Django and the standard library only (and the plugin, for ``Guarded``).
"""

from __future__ import annotations

import atexit
import json
import os
import sys
import traceback
import unittest
import warnings
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, TextIO

from django.test.runner import DiscoverRunner

INTERNAL = "QueryGuardInternalWarning"

# test id -> (outcome, message); test id -> findings; the running test
_outcomes: dict[str, tuple[str, str]] = {}
_findings: dict[str, list[dict[str, Any]]] = {}
_current: list[str] = [""]
# set once the suite ran to the end (suite_result): a run cut short, by the
# workflow's timeout say, still writes what it has, marked incomplete
_finished: list[bool] = [False]
_installed: list[bool] = [False]
_RANK = {"failed": 0, "skipped": 1, "passed": 2}


def _out(name: str) -> Path | None:
    directory = os.environ.get("REALWORLD_OUT")
    return Path(directory) / name if directory else None


def _note(test: Any, outcome: str, message: str = "") -> None:
    test_id = test.id()
    previous = _outcomes.get(test_id)
    if previous is None or _RANK[outcome] < _RANK[previous[0]]:
        _outcomes[test_id] = (outcome, message)


def _error_text(err: Any) -> str:
    try:
        return "".join(traceback.format_exception_only(err[0], err[1])).strip()
    except Exception:  # noqa: BLE001  # an odd error tuple: its repr
        return repr(err)


class Recording(unittest.TextTestResult):
    """Notes the outcome of every test, as pytest's JUnit XML would."""

    def startTest(self, test: Any) -> None:  # unittest's name
        _current[0] = test.id()
        super().startTest(test)

    def addSuccess(self, test: Any) -> None:  # unittest's name
        _note(test, "passed")
        super().addSuccess(test)

    def addFailure(self, test: Any, err: Any) -> None:  # unittest's name
        _note(test, "failed", _error_text(err))
        super().addFailure(test, err)

    def addError(self, test: Any, err: Any) -> None:  # unittest's name
        _note(test, "failed", _error_text(err))
        super().addError(test, err)

    def addSkip(self, test: Any, reason: str) -> None:  # unittest's name
        _note(test, "skipped", reason)
        super().addSkip(test, reason)

    def addExpectedFailure(self, test: Any, err: Any) -> None:  # unittest's name
        _note(test, "skipped", "expected failure")
        super().addExpectedFailure(test, err)

    def addUnexpectedSuccess(self, test: Any) -> None:  # unittest's name
        _note(test, "failed", "unexpected success")
        super().addUnexpectedSuccess(test)

    def addSubTest(self, test: Any, subtest: Any, err: Any) -> None:  # unittest's name
        if err is not None:
            _note(test, "failed", _error_text(err))
        super().addSubTest(test, subtest, err)


def write_junit(path: Path) -> None:
    """The outcomes noted so far, as JUnit XML (classname::name per test)."""
    suite = ET.Element("testsuite", name="manage.py test", tests=str(len(_outcomes)))
    for test_id, (outcome, message) in sorted(_outcomes.items()):
        classname, _, name = test_id.rpartition(".")
        case = ET.SubElement(suite, "testcase", classname=classname, name=name)
        if outcome == "failed":
            ET.SubElement(case, "failure", message=message[:2000])
        elif outcome == "skipped":
            ET.SubElement(case, "skipped", message=message[:2000])
    root = ET.Element("testsuites")
    root.append(suite)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def write_counts(path: Path) -> None:
    """The findings of each test, in the keys of ``--queryguard-json``."""
    tests: dict[str, Any] = {}
    for test_id in sorted(_outcomes):
        classname, _, name = test_id.rpartition(".")
        tests[f"{classname}::{name}"] = {"findings": _findings.get(test_id, [])}
    data = {"format": 1, "complete": _finished[0] and not _disabled(), "tests": tests}
    path.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")


def _disabled() -> bool:
    """Whether the plugin disabled itself after an internal error."""
    errors = sys.modules.get("pytest_queryguard.errors")
    return errors is not None and errors.internal_error() is not None


class _Recorded(DiscoverRunner):
    """A runner that writes the outcomes of its run (``REALWORLD_RUN``).

    The files are written at exit, so a run that a timeout interrupts still
    leaves them; never from a frame around the run: a frame of this file on
    the stack while the tests run would be a frame of the application to the
    plugin, and make the statements of the test code the application's.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        atexit.register(self._write)

    def get_resultclass(self) -> Any:
        return Recording

    def suite_result(self, suite: Any, result: Any, **kwargs: Any) -> Any:
        _finished[0] = True
        return super().suite_result(suite, result, **kwargs)

    def _write(self) -> None:
        path = _out(f"{os.environ.get('REALWORLD_RUN', 'run')}.xml")
        if path is not None:
            write_junit(path)


class Baseline(_Recorded):
    """Django's ``DiscoverRunner``, recording outcomes."""


def _probe(message: Warning | str, filename: str, lineno: int) -> None:
    path = _out("probe.jsonl")
    if path is None:
        return
    record = {
        "event": "internal-warning",
        "message": str(message),
        "file": filename,
        "line": lineno,
        "nodeid": _current[0],
    }
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def _install_recorders() -> None:
    """Record the findings the runner makes and its internal warnings.

    The findings are those of the runner's application side (its ``_detect``
    with ``test_code`` other than True), less the test code's repetition: what
    it reports with its default settings. Internal, so tied to the plugin's
    version; a project's own ``QUERYGUARD["ignore"]`` is not applied.
    """
    if _installed[0]:
        return
    _installed[0] = True
    import pytest_queryguard.django as runner_module
    from pytest_queryguard.messages import make_findings

    detect = getattr(runner_module, "_detect")  # noqa: B009  # internal

    def recording(collector: Any, config: Any, sieve: Any, test_code: Any) -> Any:
        found = detect(collector, config, sieve, test_code)
        if test_code is not True:
            _findings.setdefault(_current[0], []).extend(
                {
                    "kind": f.kind,
                    "label": f.label,
                    "file": f.file,
                    "line": f.line,
                    "function": f.function,
                    "count": f.count,
                }
                for f in make_findings(found)
                if not f.test_code
            )
        return found

    setattr(runner_module, "_detect", recording)  # noqa: B010
    warnings.simplefilter("always", runner_module.QueryGuardInternalWarning)
    shown = warnings.showwarning

    def showwarning(
        message: Warning | str,
        category: type[Warning],
        filename: str,
        lineno: int,
        file: TextIO | None = None,
        line: str | None = None,
    ) -> None:
        if category.__name__ == INTERNAL:
            _probe(message, filename, lineno)
        shown(message, category, filename, lineno, file, line)

    warnings.showwarning = showwarning


def _guarded() -> type:
    # imported only for the run with the plugin: the baseline run does not
    # import any of pytest_queryguard
    from pytest_queryguard.django import QueryGuardRunner

    class Guarded(QueryGuardRunner, _Recorded):
        """``QueryGuardRunner``, recording outcomes, findings and internal warnings."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            _install_recorders()
            super().__init__(*args, **kwargs)

        def _write(self) -> None:
            super()._write()
            path = _out("queryguard.json")
            if path is not None:
                write_counts(path)
            if _disabled() and not (_out("probe.jsonl") or Path()).exists():
                # the warning was filtered out: the error is recorded anyway
                _probe(
                    "pytest-queryguard disabled itself after an internal error", "", 0
                )

    return Guarded


def __getattr__(name: str) -> Any:
    """``realworld_runner.Guarded``, made on first use (``--testrunner``)."""
    if name == "Guarded":
        guarded = _guarded()
        globals()["Guarded"] = guarded
        return guarded
    raise AttributeError(name)
