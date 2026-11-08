"""BUG column-load: a template prints ``{{ book.pages }}`` of books loaded with ``.only("title")``."""

from django.template import Context, Template

from ...library.models import Book
from ..kit import BUG, COLUMN

VERDICT = BUG
KINDS = frozenset({COLUMN})
TEMPLATE = Template(
    "{% for book in books %}{{ book.title }}: {{ book.pages }};{% endfor %}"
)


def run() -> None:
    text = TEMPLATE.render(Context({"books": Book.objects.only("title")}))
    assert text.count(";") == 18
