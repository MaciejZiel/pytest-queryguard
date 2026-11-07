"""BUG repeated-statement, write-loop: ``get_or_create`` for each name.

A SELECT and, for new names, an INSERT per item: look the names up with one
query and ``bulk_create`` the missing ones.
"""

from ...library.models import Tag
from ..kit import BUG, REPEAT, WRITE

VERDICT = BUG
KINDS = frozenset({REPEAT, WRITE})


def run() -> None:
    for name in ("tag 0", "tag 1", "new a", "new b", "new c", "new d"):
        Tag.objects.get_or_create(name=name)
