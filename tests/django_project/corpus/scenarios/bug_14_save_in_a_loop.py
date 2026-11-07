"""BUG write-loop: ``Model.objects.create()`` for each row of a list.

One INSERT per row; ``bulk_create`` writes them in one statement.
"""

from ...library.models import Publisher
from ..kit import BUG, WRITE

VERDICT = BUG
KINDS = frozenset({WRITE})


def run() -> None:
    for i in range(5):
        Publisher.objects.create(name=f"imported {i}")
