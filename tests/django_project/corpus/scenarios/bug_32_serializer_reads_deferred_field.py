"""BUG column-load: a DRF serializer outputs ``body``, which the queryset defers.

``.defer("body")`` to make a list cheaper, and a serializer that still
renders the field: one SELECT per book for its body.
"""

from rest_framework import serializers

from ...library.models import Book
from ..kit import BUG, COLUMN

VERDICT = BUG
KINDS = frozenset({COLUMN})


class BookText(serializers.ModelSerializer):
    class Meta:
        model = Book
        fields = ["id", "title", "body"]


def run() -> None:
    data = BookText(Book.objects.defer("body"), many=True).data
    assert len(data) == 18
