from __future__ import annotations

import pytest


def test_plugin_registers_markers(pytester: pytest.Pytester) -> None:
    result = pytester.runpytest("--markers")
    result.stdout.fnmatch_lines(["*max_queries(limit)*", "*allow_nplusone*"])
