"""CLEAN: Factory Boy ``create_batch(10)`` inside the test body.

Seeding rows is not an application bug. Each created object is flushed, so the
INSERTs are single-row statements from one line: the opt-in write-loop rule
cannot tell test seeding from application code.
"""

import factory
from factory.alchemy import SQLAlchemyModelFactory
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
REQUIRES = ("factory",)
KNOWN = {"write-loop": "factory-created rows are flushed one by one; keep write-loop for the code under test"}


def setup(ctx: Ctx) -> None:
    ctx.reset()
    shop.Base.metadata.create_all(ctx.engine())


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:

        class TagFactory(SQLAlchemyModelFactory):
            class Meta:
                model = shop.Tag
                sqlalchemy_session = session
                sqlalchemy_session_persistence = "flush"

            name = factory.Sequence(lambda n: f"tag{n}")

        TagFactory.create_batch(10)
        session.commit()
        names = list(session.scalars(select(shop.Tag.name)))
    assert len(names) == 10
