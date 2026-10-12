from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import IndexedOrder, Order


def orders_of(session: Session, customer_id: int) -> Sequence[Order]:
    stmt = select(Order).where(Order.customer_id == customer_id)
    return session.scalars(stmt).all()


def indexed_orders_of(session: Session, customer_id: int) -> Sequence[IndexedOrder]:
    stmt = select(IndexedOrder).where(IndexedOrder.customer_id == customer_id)
    return session.scalars(stmt).all()
