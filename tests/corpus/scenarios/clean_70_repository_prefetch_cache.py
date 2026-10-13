"""CLEAN: a repository that warms its cache with one ``IN`` query.

``get_many`` loads all the ids a view needs at once; every ``get`` afterwards is
served from the cache. The fix of ``bug_54_repository_cache_per_id``.
"""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
setup = shop.setup


class ProductRepository:
    def __init__(self, session: Session) -> None:
        self.session = session
        self._cache: dict[int, shop.Product] = {}

    def get_many(self, ids: Iterable[int]) -> None:
        missing = [i for i in ids if i not in self._cache]
        if missing:
            stmt = select(shop.Product).where(shop.Product.id.in_(missing))
            self._cache.update((p.id, p) for p in self.session.scalars(stmt))

    def get(self, product_id: int) -> shop.Product:
        if product_id not in self._cache:
            self.get_many([product_id])
        return self._cache[product_id]


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        repo = ProductRepository(session)
        repo.get_many(range(1, 11))
        names = [repo.get(i).name for _ in range(3) for i in range(1, 11)]
    assert len(names) == 30
