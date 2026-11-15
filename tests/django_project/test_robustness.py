"""A bug in the plugin never breaks the code under test; strict mode raises.

Each test that forces an internal error checks that the application's own
result is right, that the error is recorded (measurement stops, as for the
SQLAlchemy listeners), and puts the recorded error back afterwards.
"""

from __future__ import annotations

import copy
import pickle
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from pytest_queryguard import django_backend, listeners
from pytest_queryguard.events import RelationshipLoadEvent

from .helpers import measure
from .library.models import Author, Book

pytestmark = [
    pytest.mark.allow_nplusone,
    pytest.mark.usefixtures("seeded"),
    # the outer measurement of the test sees the forced error too
    pytest.mark.filterwarnings(
        "ignore::pytest_queryguard.warnings.QueryGuardInternalWarning"
    ),
]


class Boom(Exception):
    pass


def _boom(*args: object, **kwargs: object) -> None:
    raise Boom


@pytest.fixture
def recorded() -> Iterator[list[BaseException | None]]:
    """The internal error the test caused, reset for the tests after it."""
    before = listeners.swap_error(None)
    seen: list[BaseException | None] = []
    yield seen
    seen.append(listeners.swap_error(before))


@pytest.mark.parametrize(
    "name, code",
    [
        ("_record", lambda: Author.objects.count()),
        ("_relationship_event", lambda: [b.author.name for b in Book.objects.all()]),
        ("_mark", lambda: [len(a.books.all()) for a in Author.objects.all()]),
        ("_rows_origin", lambda: [b.title for b in Book.objects.all()]),
        ("_key", lambda: [b.body for b in Book.objects.only("title")]),
        ("_child_origin", lambda: [len(a.books.all()) for a in Author.objects.all()]),
        # the scope of a forward or reverse one-to-one load
        ("_scope", lambda: [b.author.name for b in Book.objects.all()]),
        ("_scope", lambda: [a.profile.bio for a in Author.objects.all()]),
    ],
)
def test_a_plugin_bug_does_not_reach_the_application(
    name: str,
    code: object,
    recorded: list[BaseException | None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(django_backend, name, _boom)
    with measure():
        result = code()  # type: ignore[operator]
    assert result
    assert isinstance(listeners.internal_error(), Boom)
    with measure() as collector:
        assert Author.objects.count() == 6
    assert collector.statements == []  # stopped after the error


def test_a_bug_after_the_statement_keeps_its_result(
    recorded: list[BaseException | None], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(django_backend, "_index_check", True)
    monkeypatch.setattr(django_backend, "_check_index", _boom)
    with measure():
        assert Author.objects.count() == 6
    assert isinstance(listeners.internal_error(), Boom)


def test_a_bug_while_hooking_in(
    recorded: list[BaseException | None], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(django_backend, "_attach_all", _boom)
    django_backend.ensure()
    assert isinstance(listeners.internal_error(), Boom)


def _strict(load: RelationshipLoadEvent, *args: object) -> BaseException:
    return AssertionError(f"strict: {load.relationship}")


@pytest.mark.parametrize(
    "code, label",
    [
        (lambda: [b.author.name for b in Book.objects.all()], "Book.author"),
        (lambda: [len(a.books.all()) for a in Author.objects.all()], "Author.books"),
        (lambda: [a.profile.bio for a in Author.objects.all()], "Author.profile"),
    ],
)
def test_strict_mode_raises_before_the_select(code: object, label: str) -> None:
    with measure(strict=_strict) as collector, pytest.raises(AssertionError) as raised:
        code()  # type: ignore[operator]
    assert str(raised.value) == f"strict: {label}"
    assert raised.value.__suppress_context__
    assert collector.strict_raised
    loads = [
        load for load in collector.relationship_loads if load.relationship == label
    ]
    # the first parent's load ran its SELECT, the second raised before it
    assert len(loads) == 2
    lazy = [s for s in collector.statements if s.relationship == label]
    assert len(lazy) == 1


def test_a_stale_generic_target_is_loaded_again() -> None:
    from django.contrib.contenttypes.models import ContentType

    from .library.models import Bookmark

    book_type = ContentType.objects.get_for_model(Book)
    Bookmark.objects.bulk_create(
        [Bookmark(content_type=book_type, object_id=pk) for pk in range(1, 7)]
    )
    marks = list(Bookmark.objects.prefetch_related("target"))
    with measure() as collector:
        for mark in marks:
            assert mark.target.pk == mark.object_id  # cached: no query
            mark.object_id += 6
            assert mark.target.pk == mark.object_id  # stale: loaded again
    assert len(collector.relationship_loads) == 6
    assert {load.relationship for load in collector.relationship_loads} == {
        "Bookmark.target"
    }


def test_another_database_gets_the_unsupported_notice() -> None:
    with measure(index_check=True) as collector:
        context = {"connection": SimpleNamespace(vendor="oracle"), "cursor": None}
        django_backend._check_index("SELECT 1", None, False, context)
    assert list(collector.index_notices) == [
        "oracle statements were not checked: the index check supports "
        + __import__("pytest_queryguard.indexes").indexes.SUPPORTED_DATABASES
    ]


def test_marks_and_origins_pickle_as_plain_objects() -> None:
    with measure():
        books = list(Book.objects.all())
        related = books[0].author.books.all()
    state = books[0]._state.__dict__
    origin = state["_queryguard_origin"]
    mark = related.query.__dict__["_queryguard_load"]
    assert copy.copy(origin) is origin and copy.deepcopy(mark) is mark
    restored = pickle.loads(pickle.dumps(books[0]))
    assert type(restored._state.__dict__["_queryguard_origin"]) is object
    assert type(pickle.loads(pickle.dumps(mark))) is object


def test_closing_the_rows_early_closes_djangos_generator() -> None:
    # Django's generator closes its cursor in a finally block
    closed: list[bool] = []

    def rows() -> Iterator[Book]:
        try:
            yield from Book.objects.all()[:2]
        finally:
            closed.append(True)

    inner = rows()  # kept here: not closed by being garbage-collected
    with measure():
        noted = django_backend._noting(inner, Book.objects.all())
        next(noted)
        noted.close()  # type: ignore[attr-defined]
    assert closed == [True]


def test_the_names_of_django_and_asgiref_the_call_sites_rely_on() -> None:
    # The view a generic view's statements are attributed to is the local
    # "callback" of BaseHandler._get_response (and of its async twin), and the
    # thread of an async ORM call starts at SyncToAsync.thread_handler. A
    # release that renames them turns that off silently: this test says so.
    from asgiref.sync import SyncToAsync
    from django.core.handlers.base import BaseHandler

    for name in ("_get_response", "_get_response_async", "get_response"):
        assert hasattr(getattr(BaseHandler, name), "__code__"), name
    for name in ("_get_response", "_get_response_async"):
        assert "callback" in getattr(BaseHandler, name).__code__.co_varnames, name
    assert hasattr(SyncToAsync.thread_handler, "__code__")


def test_the_view_of_a_request_is_found_or_left_unknown() -> None:
    from django.views import View

    from .library.views import AuthorList, book_list_later

    locate = django_backend._locate_view
    assert locate(book_list_later)[1:] == (
        book_list_later.__code__.co_firstlineno,
        "book_list_later",
    )
    file, line, name = locate(AuthorList.as_view())
    assert (Path(file).name, name, line > 0) == ("views.py", "AuthorList", True)
    assert locate(object()) is None  # a callable with no code of its own
    # a class made at run time has no source to find: unknown, no error
    made = type("Made", (View,), {"__module__": __name__})
    frame = SimpleNamespace(f_locals={"callback": made.as_view()})
    assert django_backend._view_of(frame) is None  # type: ignore[arg-type]
    assert django_backend._view_of(frame) is None  # type: ignore[arg-type]
    assert django_backend._view_of(SimpleNamespace(f_locals={})) is None  # type: ignore[arg-type]
