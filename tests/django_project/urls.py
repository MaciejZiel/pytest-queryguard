from __future__ import annotations

from django.contrib import admin
from django.urls import path
from django.views.generic import ListView

from .library import views
from .library.models import Book

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/books/", views.BookList.as_view()),
    path("api/books-fixed/", views.BookListFixed.as_view()),
    path("api/authors/", views.AuthorList.as_view()),
    path("books/", views.book_titles),
    path("books/three/", views.three_books),
    path("books/<int:pk>/", views.one_book),
    # async views, for the async test client
    path("async/books/six/", views.six_books_async),
    path("async/books/<int:pk>/", views.one_book_async),
    # streamed responses, read from the database while they are consumed
    path("stream/async/<int:pk>/", views.stream_book_async),
    path("stream/<int:pk>/", views.stream_book),
    path("file/<int:pk>/", views.book_file),
    # a generic view: no line of the project runs, the template reads the author
    path("books/list/", ListView.as_view(model=Book)),
]
