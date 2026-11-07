"""Lazy loads of relations: what is an N+1, what is not, and what the fix says."""

from __future__ import annotations

import copy
import pickle

import django
import pytest
from django.db import models
from django.db.models import Prefetch

from pytest_queryguard.detectors import RelationshipFinding
from pytest_queryguard.messages import make_finding

from .helpers import detections, labels, measure
from .library.models import Author, Book, Fussy, Place, Tag

pytestmark = [pytest.mark.allow_nplusone, pytest.mark.usefixtures("seeded")]

REL = "relationship-load"


def _one(collector) -> RelationshipFinding:  # type: ignore[no-untyped-def]
    found = detections(collector)
    assert len(found) == 1, found
    (finding,) = found
    assert isinstance(finding, RelationshipFinding)
    return finding


def test_foreign_key_in_a_loop() -> None:
    with measure() as collector:
        names = [book.author.name for book in Book.objects.all()]  # the N+1
    assert len(names) == 18
    finding = _one(collector)
    assert finding.relationship == "Book.author"
    assert finding.count == 18
    assert finding.scalar
    assert finding.parent_site is not None
    assert finding.parent_site.function == "test_foreign_key_in_a_loop"
    assert finding.parent_root == "Book"
    assert all(s.lazy_load for s in collector.statements[1:])
    text = make_finding(finding).message
    assert 'Book.objects.select_related("author")' in text
    assert "FETCH_PEERS" in text


def test_select_related_fixes_it() -> None:
    with measure() as collector:
        names = [b.author.name for b in Book.objects.select_related("author")]
    assert len(names) == 18
    assert len(collector.statements) == 1
    assert detections(collector) == []


def test_reverse_foreign_key_in_a_loop() -> None:
    with measure() as collector:
        counts = [len(a.books.all()) for a in Author.objects.all()]
    assert counts == [3] * 6
    finding = _one(collector)
    assert finding.relationship == "Author.books"
    assert finding.count == 6
    assert not finding.scalar
    assert 'Author.objects.prefetch_related("books")' in make_finding(finding).message


def test_prefetch_related_fixes_it_and_keeps_the_cache() -> None:
    with measure() as collector:
        authors = list(Author.objects.prefetch_related("books"))
        titles = [[b.title for b in a.books.all()] for a in authors]
        counts = [a.books.count() for a in authors]
    assert counts == [3] * 6
    assert titles[0] == ["book 0.0", "book 0.1", "book 0.2"]
    assert len(collector.statements) == 2
    assert detections(collector) == []
    assert authors[0].books.all()._result_cache is not None


def test_filter_on_a_prefetched_relation_queries_again() -> None:
    with measure() as collector:
        authors = list(Author.objects.prefetch_related("books"))
        thick = [list(a.books.filter(pages__gt=100)) for a in authors]
    assert all(len(books) == 2 for books in thick)
    assert labels(collector) == {(REL, "Author.books")}


def test_count_on_a_relation_in_a_loop() -> None:
    with measure() as collector:
        counts = [a.books.count() for a in Author.objects.all()]
    assert counts == [3] * 6
    assert labels(collector) == {(REL, "Author.books")}


def test_many_to_many_both_ways() -> None:
    with measure() as collector:
        for book in Book.objects.all():
            [tag.name for tag in book.tags.all()]
        for tag in Tag.objects.all():
            [b.title for b in tag.books.all()]
    assert labels(collector) == {(REL, "Book.tags"), (REL, "Tag.books")}


def test_prefetch_objects_fix_many_to_many() -> None:
    with measure() as collector:
        books = Book.objects.prefetch_related(
            Prefetch(
                "tags", queryset=Tag.objects.order_by("name"), to_attr="sorted_tags"
            )
        )
        names = [[t.name for t in b.sorted_tags] for b in books]
    assert len(names) == 18
    assert detections(collector) == []


def test_reverse_one_to_one_in_a_loop() -> None:
    with measure() as collector:
        bios = [a.profile.bio for a in Author.objects.all()]
    assert len(bios) == 6
    finding = _one(collector)
    assert finding.relationship == "Author.profile"
    assert finding.scalar
    with measure() as collector:
        bios = [a.profile.bio for a in Author.objects.select_related("profile")]
    assert detections(collector) == []


def test_multi_table_inheritance_child_in_a_loop() -> None:
    with measure() as collector:
        pizza = [hasattr(p, "restaurant") for p in Place.objects.all()]
    assert pizza == [False, True, False, True]
    assert labels(collector) == {(REL, "Place.restaurant")}


def test_lookups_at_different_lines_are_no_n_plus_one() -> None:
    with measure() as collector:
        first = Book.objects.get(pk=1).author.name
        second = Book.objects.get(pk=5).author.name
    assert first != second
    assert detections(collector) == []


def test_lookups_at_one_line_in_a_loop_are() -> None:
    with measure() as collector:
        names = [Book.objects.get(pk=pk).author.name for pk in (1, 4, 7)]
    assert len(names) == 3
    assert labels(collector) == {(REL, "Book.author")}


def test_iterator_in_a_loop() -> None:
    with measure() as collector:
        names = [b.author.name for b in Book.objects.iterator(chunk_size=5)]
    assert len(names) == 18
    finding = _one(collector)
    assert finding.relationship == "Book.author"
    assert finding.parent_root == "Book"


def test_a_chain_names_the_whole_lookup() -> None:
    with measure() as collector:
        names = [b.author.publisher.name for b in Book.objects.all()]
    assert len(names) == 18
    found = {f.relationship: f for f in detections(collector)}
    assert set(found) == {"Book.author", "Author.publisher"}
    chained = found["Author.publisher"]
    assert chained.parent_root == "Book"
    assert 'Book.objects.select_related("author__publisher")' in (
        make_finding(chained).message
    )


@pytest.mark.skipif(django.VERSION < (6, 1), reason="fetch modes are Django 6.1+")
def test_fetch_peers_is_no_n_plus_one() -> None:
    with measure() as collector:
        books = Book.objects.fetch_mode(models.FETCH_PEERS)  # type: ignore[attr-defined]
        names = [b.author.name for b in books]
    assert len(names) == 18
    assert len(collector.statements) == 2
    assert detections(collector) == []


def test_writes_through_a_relation_are_no_lazy_load() -> None:
    tags = list(Tag.objects.all())
    with measure() as collector:
        for book in Book.objects.all():
            book.tags.set(tags[:2])
    assert {kind for kind, _ in labels(collector)} <= {"repeated-statement"}
    assert not any(s.lazy_load for s in collector.statements)


def test_instances_and_querysets_still_pickle_and_copy() -> None:
    with measure():
        books = list(Book.objects.all())
        author = books[0].author
        related = author.books.all()
        loaded = pickle.loads(pickle.dumps(books))
        again = pickle.loads(pickle.dumps(related))
        clone = copy.deepcopy(books[1])
    assert [b.pk for b in loaded] == [b.pk for b in books]
    assert [b.pk for b in again] == [1, 2, 3]
    assert clone.pk == books[1].pk


def test_instances_are_never_compared() -> None:
    owner = Author.objects.get(pk=1)
    for i in range(3):
        Fussy.objects.create(name=f"f{i}", owner=owner)
    with measure() as collector:
        names = [f.owner.name for f in Fussy.objects.all()]
        fussies = [len(a.fussies.all()) for a in Author.objects.all()]
    assert names == ["author 0"] * 3
    assert fussies == [3, 0, 0, 0, 0, 0]
    assert labels(collector) == {(REL, "Fussy.owner"), (REL, "Author.fussies")}


def test_generic_relation_in_a_loop() -> None:
    from django.contrib.contenttypes.models import ContentType

    from .library.models import Bookmark

    book_type = ContentType.objects.get_for_model(Book)
    Bookmark.objects.bulk_create(
        [Bookmark(content_type=book_type, object_id=pk) for pk in range(1, 7)]
    )
    with measure() as collector:
        counts = [len(b.bookmarks.all()) for b in Book.objects.all()]
    assert sum(counts) == 6
    assert labels(collector) == {(REL, "Book.bookmarks")}
    with measure() as collector:
        books = Book.objects.prefetch_related("bookmarks")
        counts = [len(b.bookmarks.all()) for b in books]
    assert detections(collector) == []


def test_async_manager_methods_stay_coroutine_functions() -> None:
    import inspect

    author = Author.objects.get(pk=1)
    for name in ("aadd", "aset", "acreate", "aget_or_create"):
        assert inspect.iscoroutinefunction(getattr(author.books, name)), name
    book = Book.objects.get(pk=1)
    assert inspect.iscoroutinefunction(book.tags.aset)
    assert getattr(author.books.create, "alters_data", False)
