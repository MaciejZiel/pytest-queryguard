"""CLEAN: rows written with one ``bulk_create``, in batches of two."""

from ...library.models import Publisher
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def run() -> None:
    Publisher.objects.bulk_create(
        [Publisher(name=f"bulk {i}") for i in range(6)], batch_size=2
    )
