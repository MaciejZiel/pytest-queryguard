"""CLEAN: one SELECT for all names, then ``add_all`` for the missing ones."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


def get_or_create_tags(session: Session, names: list[str]) -> list[shop.Tag]:
    existing = {
        t.name: t
        for t in session.scalars(select(shop.Tag).where(shop.Tag.name.in_(names)))
    }
    created = [shop.Tag(name=n) for n in names if n not in existing]
    session.add_all(created)
    return [existing.get(n) or next(c for c in created if c.name == n) for n in names]


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        tags = get_or_create_tags(session, [f"new{i}" for i in range(10)])
        session.commit()
    assert len(tags) == 10
