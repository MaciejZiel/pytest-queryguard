"""Each test-client request keeps its own scope across its lifecycle.

The Django rows of the matrix in tests/inapp/test_request_lifecycle.py (see
"When code scopes something to a request" in CONTRIBUTING.md): a body read
after the client returned (#386), bodies of several requests read in turn,
a HEAD request, an error half way through a body. Each request gets one
book; the six gets must be six scopes, so ``scope="session"`` sees no repeat.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from .helpers import detections, measure

pytestmark = [
    pytest.mark.allow_nplusone,
    pytest.mark.usefixtures("seeded"),
    pytest.mark.django_db(transaction=True),
]

BOOKS = range(1, 7)


def _expect_one_scope_per_request(collector: Any) -> None:
    gets = [s for s in collector.statements if "library_book" in s.sql]
    assert len(gets) == len(BOOKS)
    assert len({s.scope for s in gets}) == len(BOOKS)
    assert None not in {s.scope for s in gets}
    assert detections(collector, scope="session") == []


def _expect_test_code_outside(collector: Any) -> None:
    """The test's last statement ran in no request's scope."""
    from pytest_queryguard.collector import RequestScope

    assert not isinstance(collector.statements[-1].scope, RequestScope)


async def _read(response: Any) -> bytes:
    from asgiref.sync import sync_to_async

    if not getattr(response, "streaming", False):
        return bytes(response.content)
    if response.is_async:
        return b"".join([part async for part in response.streaming_content])
    return await sync_to_async(b"".join)(response.streaming_content)


SYNC_PATHS = ["books/{pk}/", "stream/{pk}/", "file/{pk}/"]
ASYNC_PATHS = [*SYNC_PATHS, "async/books/{pk}/", "stream/async/{pk}/"]


@pytest.mark.parametrize("order", ["each", "in-turn"])
@pytest.mark.parametrize("path", SYNC_PATHS)
def test_client_requests_keep_their_scope(path: str, order: str) -> None:
    from django.test import Client

    from .library.models import Author

    client = Client()
    with measure() as collector:
        if order == "each":
            for pk in BOOKS:
                response = client.get("/" + path.format(pk=pk))
                assert b"".join(response) if response.streaming else response.content
        else:
            # every request first, then their bodies, as a server serving
            # several clients reads them
            responses = [client.get("/" + path.format(pk=pk)) for pk in BOOKS]
            for response in responses:
                assert b"".join(response) if response.streaming else response.content
        Author.objects.count()  # the test's own, after the bodies were read
    _expect_one_scope_per_request(collector)
    _expect_test_code_outside(collector)


@pytest.mark.parametrize("order", ["each", "in-turn"])
@pytest.mark.parametrize("path", ASYNC_PATHS)
def test_async_client_requests_keep_their_scope(path: str, order: str) -> None:
    from django.test import AsyncClient

    async def requests() -> None:
        client = AsyncClient()
        if order == "each":
            for pk in BOOKS:
                assert await _read(await client.get("/" + path.format(pk=pk)))
        else:
            responses = [await client.get("/" + path.format(pk=pk)) for pk in BOOKS]
            for response in responses:
                assert await _read(response)

    with measure() as collector:
        asyncio.run(requests())
    _expect_one_scope_per_request(collector)


def test_concurrent_async_client_requests_keep_their_scope() -> None:
    from django.test import AsyncClient

    async def requests() -> None:
        client = AsyncClient()
        responses = await asyncio.gather(
            *(client.get(f"/stream/async/{pk}/") for pk in BOOKS)
        )
        for response in responses:
            assert await _read(response)

    with measure() as collector:
        asyncio.run(requests())
    _expect_one_scope_per_request(collector)


# Django warns that it reads the async body synchronously: expected here.
@pytest.mark.filterwarnings(
    "ignore:StreamingHttpResponse must consume asynchronous iterators"
)
@pytest.mark.parametrize("read", ["unread", "half", "all"])
@pytest.mark.parametrize("path", ["stream/{pk}/", "file/{pk}/", "stream/async/{pk}/"])
def test_test_code_around_a_streamed_body_is_in_no_request(
    path: str, read: str
) -> None:
    """The test's statements between the call and the body's end, or with an
    unread body, are the test's own, not the request's."""
    from django.test import Client

    from pytest_queryguard.collector import RequestScope

    from .library.models import Author

    client = Client()
    with measure() as collector:
        for pk in BOOKS:
            response = client.get("/" + path.format(pk=pk))
            Author.objects.count()  # before the body
            if read == "half":
                next(iter(response))
            elif read == "all":
                assert b"".join(response)
            Author.objects.count()  # after it
            response.close()
    authors = [s for s in collector.statements if "library_author" in s.sql]
    assert len(authors) == 2 * len(BOOKS)
    assert not any(isinstance(s.scope, RequestScope) for s in authors)
    if read != "unread":
        gets = [s for s in collector.statements if "library_book" in s.sql]
        assert len({s.scope for s in gets}) == len(gets) == len(BOOKS)
        assert all(isinstance(s.scope, RequestScope) for s in gets)


def test_head_requests_keep_their_scope() -> None:
    from django.test import Client

    client = Client()
    with measure() as collector:
        for pk in BOOKS:
            assert client.head(f"/books/{pk}/").status_code == 200
    _expect_one_scope_per_request(collector)


def test_an_error_half_way_through_a_body_ends_its_request() -> None:
    from django.test import Client

    from .library.models import Author

    client = Client()
    with measure() as collector:
        for pk in BOOKS:
            response = client.get(f"/stream/broken/{pk}/")
            with pytest.raises(RuntimeError, match="body bug"):
                b"".join(response)
        Author.objects.count()  # the test's own, after the requests
    _expect_one_scope_per_request(collector)
    _expect_test_code_outside(collector)
