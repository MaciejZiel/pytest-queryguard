"""BUG repeated-statement: a GraphQL-style field resolver that queries per parent.

The resolver for ``Customer.openOrders`` runs once for every customer in the
list, as a GraphQL executor calls field resolvers, and each call runs its own
``SELECT``. The fix is a DataLoader that collects the keys and runs one query.
"""

from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
setup = shop.setup


def resolve_open_orders(parent: shop.Customer, info: dict[str, Any]) -> list[int]:
    session: Session = info["session"]
    stmt = select(shop.Order.id).where(
        shop.Order.customer_id == parent.id, shop.Order.status == "new"
    )
    return list(session.scalars(stmt))


def execute(
    parents: list[shop.Customer],
    fields: dict[str, Callable[[shop.Customer, dict[str, Any]], object]],
    info: dict[str, Any],
) -> list[dict[str, object]]:
    """What an executor does: every field resolver for every parent object."""
    return [{name: fn(p, info) for name, fn in fields.items()} for p in parents]


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        customers = list(session.scalars(select(shop.Customer)))
        data = execute(
            customers, {"openOrders": resolve_open_orders}, {"session": session}
        )
    assert len(data) == 8 and all(len(row["openOrders"]) == 3 for row in data)
