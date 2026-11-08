"""Measuring a block of a test with a collector of its own, and its findings."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from pytest_queryguard import collector as collectors
from pytest_queryguard.collector import QueryCollector
from pytest_queryguard.detectors import Detection, detect
from pytest_queryguard.messages import make_findings


@contextmanager
def measure(**kwargs: Any) -> Iterator[QueryCollector]:
    """A collector of its own around the block, as a test's measurement."""
    collector = QueryCollector(**kwargs)
    collectors.push(collector)
    try:
        yield collector
    finally:
        collectors.pop(collector)


def detections(collector: QueryCollector, **kwargs: Any) -> list[Detection]:
    options: dict[str, Any] = {"relationship_threshold": 2, "repeat_threshold": 5}
    options.update(kwargs)
    return detect(collector, **options)


def labels(collector: QueryCollector, **kwargs: Any) -> set[tuple[str, str]]:
    """(kind, label) of every finding the default rules make."""
    return {(f.kind, f.label) for f in make_findings(detections(collector, **kwargs))}
