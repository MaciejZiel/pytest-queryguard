"""BUG repeated-statement and write-loop: ``get_or_create`` for each tag name.

The helper selects by name and inserts when missing. Called for ten names it
runs ten SELECTs and, because the next SELECT autoflushes the previous INSERT,
ten single-row INSERTs from one line.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REPEAT, WRITE, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT, WRITE})
setup = shop.setup


def get_or_create_tag(session: Session, name: str) -> shop.Tag:
    tag = session.scalar(select(shop.Tag).where(shop.Tag.name == name))
    if tag is None:
        tag = shop.Tag(name=name)
        session.add(tag)
    return tag


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        tags = [get_or_create_tag(session, f"new{i}") for i in range(10)]
        session.commit()
    assert len(tags) == 10
