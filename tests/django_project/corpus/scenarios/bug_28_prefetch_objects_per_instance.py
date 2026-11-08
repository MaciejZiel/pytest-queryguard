"""BUG repeated-statement: ``prefetch_related_objects([author], ...)`` per author.

A prefetch for one instance at a time is one query per instance: the call
belongs outside the loop, with all instances.
"""

from django.db.models import prefetch_related_objects

from ...library.models import Author
from ..kit import BUG, REPEAT

VERDICT = BUG
KINDS = frozenset({REPEAT})


def run() -> None:
    for author in Author.objects.all():
        prefetch_related_objects([author], "books")
        assert len(author.books.all()) == 3
