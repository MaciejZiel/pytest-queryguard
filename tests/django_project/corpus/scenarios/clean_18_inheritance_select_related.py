"""CLEAN: places with their restaurant rows, selected in one query."""

from ...library.models import Place
from ..kit import CLEAN

VERDICT = CLEAN
KINDS: frozenset[str] = frozenset()


def run() -> None:
    kinds = [
        hasattr(p, "restaurant") for p in Place.objects.select_related("restaurant")
    ]
    assert kinds.count(True) == 2
