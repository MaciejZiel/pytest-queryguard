"""BUG relationship-load: a template prints ``{{ book.author.name }}`` per row.

The view hands the template a plain queryset; the template loop is the N+1.
"""

from django.template import Context, Template

from ...library.models import Book
from ..kit import BUG, REL

VERDICT = BUG
KINDS = frozenset({REL})
TEMPLATE = Template("{% for book in books %}{{ book.author.name }};{% endfor %}")


def run() -> None:
    text = TEMPLATE.render(Context({"books": Book.objects.all()}))
    assert text.count(";") == 18
