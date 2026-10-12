from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Customer


def fetch_customers(session: Session, ids: list[int]) -> Sequence[Customer]:
    return session.scalars(select(Customer).where(Customer.id.in_(ids))).all()


def rename_customers(session: Session, names: dict[int, str]) -> None:
    customers = session.scalars(select(Customer).where(Customer.id.in_(names)))
    for customer in customers:
        customer.name = names[customer.id]
    session.flush()
