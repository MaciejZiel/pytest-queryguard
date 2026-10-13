"""BUG repeated-statement: a caching repository whose cache never hits.

``ProductRepository.get`` caches by id, but the view asks for ten different ids
once each, so every call is a miss and a query. The cache hides the loop from a
reader; a ``get_many`` with one ``IN`` query is the fix.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
setup = shop.setup


class ProductRepository:
    def __init__(self, session: Session) -> None:
        self.session = session
        self._cache: dict[int, shop.Product] = {}

    def get(self, product_id: int) -> shop.Product:
        if product_id not in self._cache:
            self._cache[product_id] = self.session.scalars(
                select(shop.Product).where(shop.Product.id == product_id)
            ).one()
        return self._cache[product_id]


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        repo = ProductRepository(session)
        names = [repo.get(i).name for i in range(1, 11)]
    assert len(names) == 10
