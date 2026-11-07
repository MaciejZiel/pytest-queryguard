from __future__ import annotations

from django.contrib import admin

from .models import Author, Book, FastBook


@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    # a method, not a field: the admin does not select the author by itself
    list_display = ("title", "author_name")

    @admin.display(description="author")
    def author_name(self, obj: Book) -> str:
        return obj.author.name


@admin.register(FastBook)
class FastBookAdmin(BookAdmin):
    list_select_related = ("author",)


admin.site.register(Author)
