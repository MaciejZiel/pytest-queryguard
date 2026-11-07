"""BUG relationship-load: the books of every tag, read from the tag's side.

The reverse accessor of a many-to-many field (``tag.books``) in a loop.
"""

from ...library.models import Tag
from ..kit import BUG, REL

VERDICT = BUG
KINDS = frozenset({REL})


def run() -> None:
    sizes = [tag.books.count() for tag in Tag.objects.all()]
    assert sum(sizes) == 36
