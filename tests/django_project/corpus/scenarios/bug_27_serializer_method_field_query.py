"""BUG repeated-statement: a DRF ``SerializerMethodField`` that queries per row.

``get_review_count`` filters the reviews of each book with its own query.
"""

from rest_framework import serializers

from ...library.models import Book, Review
from ..kit import BUG, REPEAT

VERDICT = BUG
KINDS = frozenset({REPEAT})


class BookStats(serializers.ModelSerializer):
    review_count = serializers.SerializerMethodField()

    class Meta:
        model = Book
        fields = ["id", "review_count"]

    def get_review_count(self, book: Book) -> int:
        return Review.objects.filter(book_id=book.pk).count()


def run() -> None:
    data = BookStats(Book.objects.all(), many=True).data
    assert len(data) == 18
