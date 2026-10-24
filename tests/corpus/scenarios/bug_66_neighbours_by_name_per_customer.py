"""BUG repeated-statement: alphabetical neighbours, queried once per customer.

For every customer (in name order) the two customers that follow it by name
are loaded with ``WHERE name > :name ORDER BY name LIMIT 2``. The bound is a
string that only grows, as in keyset pagination over names, but every
statement starts from a row of the loop: eight statements for eight customers.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        customers = session.scalars(
            select(shop.Customer).order_by(shop.Customer.name)
        ).all()
        neighbours = {
            c.id: session.scalars(
                select(shop.Customer.id)
                .where(shop.Customer.name > c.name)
                .order_by(shop.Customer.name)
                .limit(2)
            ).all()
            for c in customers
        }
    assert len(neighbours) == 8
