"""BUG relationship-load, repeated-statement: a lookup helper called in a loop.

The counterpart of clean_37: the helper gets a book and reads its author, and
it runs once per book from one line of a loop, so the lookups at its line
share their call path.
"""

from ...library.models import Book
from ..kit import BUG, REL, REPEAT

VERDICT = BUG
KINDS = frozenset({REL, REPEAT})
KNOWN = {
    "strict": (
        "strict mode raises at the second lazy load of Book.author, before "
        "the five gets the repeated-statement rule needs"
    )
}


def author_of(pk: int) -> str:
    return Book.objects.get(pk=pk).author.name


def handle() -> list[str]:
    return [author_of(pk) for pk in range(1, 7)]


def run() -> None:
    assert all(handle())
