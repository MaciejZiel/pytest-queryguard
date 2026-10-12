from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Order


def list_orders(session: Session) -> list[dict[str, object]]:
    orders = session.scalars(select(Order).options(joinedload(Order.customer))).all()
    return [{"id": order.id, "customer": order.customer.name} for order in orders]
