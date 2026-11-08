"""BUG relationship-load: the child row of each place (multi-table inheritance).

``place.restaurant`` is a reverse one-to-one to the child table: one SELECT per
place, including the places that are no restaurant.
"""

from ...library.models import Place
from ..kit import BUG, REL

VERDICT = BUG
KINDS = frozenset({REL})


def run() -> None:
    kinds = [
        "restaurant" if hasattr(p, "restaurant") else "place"
        for p in Place.objects.all()
    ]
    assert kinds.count("restaurant") == 2
