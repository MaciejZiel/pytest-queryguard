"""BUG repeated-statement: a loop alternates between two store methods, a Session each.

The loop of ``bug_124_lookup_through_two_helpers`` over a store that opens a
Session per method: even invoice lines go through ``price_line``, odd ones
through ``discount_line``, and both look the product up. The calls come from
two stacks, four from each, but from one loop: still one SELECT per line,
which an ``IN`` lookup before the loop would replace. Sessions are counted on
their own only when no other Session began from the same lines; these do not
split the loop in two.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
KNOWN = {
    "session-scope": "by design: with queryguard_scope = session each Session is counted on its own, and each runs one lookup"
}
setup = shop.setup


class InvoiceStore:
    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx

    def _product(self, session: Session, product_id: int) -> shop.Product:
        return session.scalars(
            select(shop.Product).where(shop.Product.id == product_id)
        ).one()

    def price_line(self, product_id: int, qty: int) -> str:
        with Session(self.ctx.engine()) as session:
            product = self._product(session, product_id)
            return f"{product.name} x{qty}: {product.price * qty}"

    def discount_line(self, product_id: int, qty: int) -> str:
        with Session(self.ctx.engine()) as session:
            product = self._product(session, product_id)
            return f"{product.name} x{qty}: {product.price * qty * 9 // 10} (-10%)"


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        rows = session.execute(
            select(shop.OrderLine.product_id, shop.OrderLine.qty)
            .where(shop.OrderLine.order_id <= 4)
            .order_by(shop.OrderLine.id)
        ).all()
    store = InvoiceStore(ctx)
    invoice = []
    for n, (product_id, qty) in enumerate(rows):
        if n % 2 == 0:
            invoice.append(store.price_line(product_id, qty))
        else:
            invoice.append(store.discount_line(product_id, qty))
    assert len(invoice) == 8
