from __future__ import annotations

from django.http import HttpRequest, JsonResponse
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
