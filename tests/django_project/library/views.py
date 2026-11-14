from __future__ import annotations

import io
from collections.abc import AsyncIterator, Iterator

from django.http import FileResponse, HttpRequest, JsonResponse, StreamingHttpResponse
from rest_framework import generics, serializers

from .models import Author, Book


class BookSerializer(serializers.ModelSerializer):
    author_name = serializers.CharField(source="author.name")

    class Meta:
        model = Book
        fields = ["id", "title", "author_name"]


class BookList(generics.ListAPIView):
    queryset = Book.objects.all()
    serializer_class = BookSerializer


class BookListFixed(generics.ListAPIView):
    queryset = Book.objects.select_related("author")
    serializer_class = BookSerializer


class TitleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Book
        fields = ["id", "title"]


class AuthorSerializer(serializers.ModelSerializer):
    books = TitleSerializer(many=True)

    class Meta:
        model = Author
        fields = ["id", "name", "books"]


class AuthorList(generics.ListAPIView):
    queryset = Author.objects.all()
    serializer_class = AuthorSerializer


def book_titles(request: HttpRequest) -> JsonResponse:
    books = Book.objects.all()
    return JsonResponse({"books": [f"{b.title} by {b.author.name}" for b in books]})


def three_books(request: HttpRequest) -> JsonResponse:
    titles = [Book.objects.get(pk=pk).title for pk in (1, 2, 3)]
    return JsonResponse({"titles": titles})


def one_book(request: HttpRequest, pk: int) -> JsonResponse:
    book = Book.objects.get(pk=pk)
    return JsonResponse({"title": book.title, "author": book.author.name})


async def one_book_async(request: HttpRequest, pk: int) -> JsonResponse:
    book = await Book.objects.select_related("author").aget(pk=pk)
    return JsonResponse({"title": book.title, "author": book.author.name})


async def six_books_async(request: HttpRequest) -> JsonResponse:
    titles = [(await Book.objects.aget(pk=pk)).title for pk in range(1, 7)]
    return JsonResponse({"titles": titles})


async def stream_book_async(request: HttpRequest, pk: int) -> StreamingHttpResponse:
    async def title() -> AsyncIterator[str]:
        yield (await Book.objects.aget(pk=pk)).title

    return StreamingHttpResponse(title())


def stream_book(request: HttpRequest, pk: int) -> StreamingHttpResponse:
    def title() -> Iterator[str]:
        yield Book.objects.get(pk=pk).title

    return StreamingHttpResponse(title())


class _BookFile(io.RawIOBase):
    """A file whose content is a book's title, read from the database."""

    def __init__(self, pk: int) -> None:
        self.pk = pk
        self.done = False

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        if self.done:
            return b""
        self.done = True
        return Book.objects.get(pk=self.pk).title.encode()


def book_file(request: HttpRequest, pk: int) -> FileResponse:
    return FileResponse(_BookFile(pk))
