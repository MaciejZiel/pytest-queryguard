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
    # a generic view: no line of the project runs, the template reads the author
    path("books/list/", ListView.as_view(model=Book)),
]
