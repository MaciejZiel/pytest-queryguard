"""BUG write-loop: a ``post_save`` receiver writes a row for every saved object.

The loop saves the books one by one and the signal adds a review for each:
two write loops from one loop of the application.
"""

from django.db.models.signals import post_save

from ...library.models import Book, Review
from ..kit import BUG, WRITE

VERDICT = BUG
KINDS = frozenset({WRITE})


def _review(sender: type, instance: Book, created: bool, **kwargs: object) -> None:
    if created:
        Review.objects.create(book=instance, stars=5)


def run() -> None:
    post_save.connect(_review, sender=Book)
    try:
        for i in range(4):
            Book.objects.create(title=f"signed {i}", author_id=1)
    finally:
        post_save.disconnect(_review, sender=Book)
