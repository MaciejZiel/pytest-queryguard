"""Test code of clean_138: a factory that looks a tag up per product it creates."""

import factory
from factory.alchemy import SQLAlchemyModelFactory
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.corpus.domains import shop
from tests.corpus.kit import Ctx
from tests.corpus.scenarios.clean_138_factory_lookup_per_object import product_names


def get_tag(session: Session, name: str) -> shop.Tag:
    return session.scalars(select(shop.Tag).where(shop.Tag.name == name)).one()


class ProductFactory(SQLAlchemyModelFactory):
    class Meta:
        model = shop.Product
        sqlalchemy_session_persistence = "flush"

    id = factory.Sequence(lambda n: 1000 + n)
    name = factory.Sequence(lambda n: f"new product {n}")

    @factory.post_generation
    def tags(self, create, extracted, **kwargs):  # type: ignore[no-untyped-def]
        session = ProductFactory._meta.sqlalchemy_session
        self.tags.append(get_tag(session, "tag1"))


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        ProductFactory._meta.sqlalchemy_session = session
        ProductFactory.create_batch(6)
        session.commit()
        assert len(product_names(session)) == 18
