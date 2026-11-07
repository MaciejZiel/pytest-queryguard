"""Fields left out by .only() and .defer(), read one instance at a time."""

from __future__ import annotations

import django
import pytest
from django.db import models

from pytest_queryguard.detectors import ColumnLoadFinding
from pytest_queryguard.messages import make_finding

from .helpers import detections, labels, measure
from .library.models import Book

pytestmark = [pytest.mark.allow_nplusone, pytest.mark.usefixtures("seeded")]


def test_a_field_left_out_by_only_read_in_a_loop() -> None:
    with measure() as collector:
        bodies = [b.body for b in Book.objects.only("title")]
    assert len(bodies) == 18
    (finding,) = detections(collector)
    assert isinstance(finding, ColumnLoadFinding)
    assert finding.label == "Book.body"
    assert finding.instances == 18
    assert all(s.column_load for s in collector.statements[1:])
    text = make_finding(finding).message
    assert "deferred field" in text
    assert 'Book.objects.only(..., "body")' in text


def test_a_deferred_field_read_in_a_loop() -> None:
    with measure() as collector:
        pages = [b.pages for b in Book.objects.defer("pages", "body")]
    assert len(pages) == 18
    assert labels(collector) == {("column-load", "Book.pages")}


def test_a_few_reads_are_below_the_threshold() -> None:
    with measure() as collector:
        bodies = [b.body for b in Book.objects.only("title")[:3]]
    assert len(bodies) == 3
    assert detections(collector) == []


def test_loading_the_field_with_the_query_is_clean() -> None:
    with measure() as collector:
        bodies = [b.body for b in Book.objects.only("title", "body")]
    assert len(bodies) == 18
    assert len(collector.statements) == 1


def test_a_deferred_foreign_key_is_both_loads() -> None:
    with measure() as collector:
        names = [b.author.name for b in Book.objects.only("title")]
    assert len(names) == 18
    assert labels(collector) == {
        ("column-load", "Book.author"),
        ("relationship-load", "Book.author"),
    }


@pytest.mark.skipif(django.VERSION < (6, 1), reason="fetch modes are Django 6.1+")
def test_fetch_peers_loads_the_field_for_all_at_once() -> None:
    with measure() as collector:
        books = Book.objects.only("title").fetch_mode(models.FETCH_PEERS)  # type: ignore[attr-defined]
        bodies = [b.body for b in books]
    assert len(bodies) == 18
    assert len(collector.statements) == 2
    assert detections(collector) == []
