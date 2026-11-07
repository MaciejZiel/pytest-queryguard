"""BUG relationship-load: ``author.profile`` (a reverse one-to-one) per author.

The reverse side of a ``OneToOneField`` queries for each instance unless the
query selects it with ``select_related("profile")``.
"""

from ...library.models import Author
from ..kit import BUG, REL

VERDICT = BUG
KINDS = frozenset({REL})


def run() -> None:
    bios = [author.profile.bio for author in Author.objects.all()]
    assert len(bios) == 6
