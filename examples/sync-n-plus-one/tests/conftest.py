from collections.abc import Iterator

import pytest
from app.models import Base, Customer, Order
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(Customer(id=i, name=f"customer {i}") for i in range(1, 51))
        session.add_all(Order(id=i, customer_id=i) for i in range(1, 51))
        session.commit()
        yield session
