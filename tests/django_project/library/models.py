"""The test project's models: a small library, with every kind of relation."""

from __future__ import annotations

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class Publisher(models.Model):
    name = models.CharField(max_length=100)

    class Meta:
        ordering = ["id"]


class Author(models.Model):
    name = models.CharField(max_length=100)
    publisher = models.ForeignKey(
        Publisher, null=True, on_delete=models.SET_NULL, related_name="authors"
    )

    class Meta:
        ordering = ["id"]


class AuthorProfile(models.Model):
    author = models.OneToOneField(
        Author, on_delete=models.CASCADE, related_name="profile"
    )
    bio = models.TextField(default="")


class Tag(models.Model):
    name = models.CharField(max_length=50)

    class Meta:
        ordering = ["id"]


class Book(models.Model):
    title = models.CharField(max_length=200)
    body = models.TextField(default="")
    pages = models.IntegerField(default=100)
    author = models.ForeignKey(Author, on_delete=models.CASCADE, related_name="books")
    tags = models.ManyToManyField(Tag, related_name="books", blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return self.title


class FastBook(Book):
    """The same table, with an admin that selects the author with the books."""

    class Meta:
        proxy = True


class Review(models.Model):
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="reviews")
    stars = models.IntegerField(default=3)

    class Meta:
        ordering = ["id"]


class Place(models.Model):
    name = models.CharField(max_length=100)

    class Meta:
        ordering = ["id"]


class Restaurant(Place):
    """Multi-table inheritance: ``place.restaurant`` is a reverse one-to-one."""

    serves_pizza = models.BooleanField(default=False)


class Fussy(models.Model):
    """A model whose instances refuse to be compared: the plugin must not."""

    name = models.CharField(max_length=50)
    owner = models.ForeignKey(Author, on_delete=models.CASCADE, related_name="fussies")

    def __eq__(self, other: object) -> bool:
        raise AssertionError("an instance of Fussy was compared")

    __hash__ = models.Model.__hash__


class Bookmark(models.Model):
    """A generic foreign key: to a book, an author or anything else."""

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    target = GenericForeignKey("content_type", "object_id")

    class Meta:
        ordering = ["id"]
