from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Order


def list_orders(session: Session) -> list[dict[str, object]]:
    orders = session.scalars(select(Order)).all()
    return [{"id": order.id, "customer": order.customer.name} for order in orders]
