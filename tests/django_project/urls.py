from __future__ import annotations

from django.contrib import admin
from django.urls import path

from .library import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/books/", views.BookList.as_view()),
    path("api/books-fixed/", views.BookListFixed.as_view()),
    path("api/authors/", views.AuthorList.as_view()),
    path("books/", views.book_titles),
]
