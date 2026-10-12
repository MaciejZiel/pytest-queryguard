"""CLEAN: the Jinja page from the lazy scenario, over ``selectinload(Author.books)``."""

from jinja2 import Template
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..domains import library
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = library.setup

PAGE = Template(
    "{% for a in authors %}<h2>{{ a.name }}</h2>"
    "{% for b in a.books %}<li>{{ b.title }}</li>{% endfor %}{% endfor %}"
)


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        stmt = select(library.Author).options(selectinload(library.Author.books))
        html = PAGE.render(authors=session.scalars(stmt).all())
    assert "<li>book18</li>" in html
